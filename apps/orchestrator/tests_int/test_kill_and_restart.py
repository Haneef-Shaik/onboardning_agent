"""RUN-2 box 1 on the compose replica set: a worker killed with SIGKILL mid-run, a restart, and the run
completes with no task's side effect applied twice (PRD FR-R6, NF-DUR, §7.2).

The worker is a real process (`kill_worker.py`: the orchestrator's own `prepare` with an executor whose
side effects go to an idempotent sink keyed by the task's `idempotency_key`). The test starts a run
through the real outbox, waits until a task is `running` and stalled, kills the process with SIGKILL
(no cleanup, no lease release), starts a second worker and waits for the run to complete. Then it reads
the sink:
- every task that ran has its effect applied exactly once (`applied.log` has no repeated key);
- tasks finished before the kill were called once; the killed task was taken over and called again, with
  the same key (`calls.log`), and its attempts and history say so;
- killed after its effect (`STALL_STAGE=after`) the retry's effect is deduplicated by the sink, not repeated.
SIGTERM then drains the second worker: it exits 0 after its task in hand.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from onb_agents.runs import RunStarter, RunStore, run_bus
from onb_agents.runs.testing import diamond_template, hire_variables_for
from onb_data import AuditLog, connect, tenant_scope
from onb_eventbus import QueueSettings

from .conftest import Scratch

ORG = "org_acme"
WORKER = Path(__file__).with_name("kill_worker.py")
LEASE_SECONDS = 1.5
KILLED_TASK = "b"


def _spawn(scratch: Scratch, directory: Path, stage: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "MONGO_URI": scratch.uri,
        "MONGO_DATABASE": scratch.name,
        "KILL_DIR": str(directory),
        "LEASE_SECONDS": str(LEASE_SECONDS),
        "STALL_TASK": KILLED_TASK,
        "STALL_STAGE": stage,
        "OTEL_TRACES_EXPORTER": "none",
    }
    return subprocess.Popen([sys.executable, str(WORKER)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _until(what: str, condition: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.1)
    pytest.fail(f"timed out after {timeout}s waiting for {what}")


def _lines(directory: Path, name: str) -> list[str]:
    path = directory / name
    return path.read_text().splitlines() if path.exists() else []


async def _start_run(scratch: Scratch) -> str:
    store = connect(scratch.uri, scratch.name)
    try:
        bus = run_bus(store, QueueSettings(lease_seconds=LEASE_SECONDS, heartbeat_seconds=LEASE_SECONDS / 3))
        await bus.ensure_indexes()
        runs = RunStore(store)
        await runs.ensure_indexes()
        starter = RunStarter(runs, bus, AuditLog(store))
        with tenant_scope(ORG):
            started = await starter.start(
                template=diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
            )
        return str(started.run["_id"])
    finally:
        await store.close()


@pytest.mark.parametrize("stage", ["before", "after"])
def test_a_worker_killed_with_sigkill_mid_run_restarts_and_the_run_completes_with_no_effect_twice(
    scratch: Scratch, tmp_path: Path, stage: str
) -> None:
    run_id = asyncio.run(_start_run(scratch))
    first = _spawn(scratch, tmp_path, stage)
    second: subprocess.Popen[bytes] | None = None
    try:
        _until("the first worker to stall on its task", lambda: (tmp_path / "stalled").exists(), timeout=60)

        def siblings_settled() -> bool:
            """Every task but the stalled one has finished or not started: nothing else is in flight, so
            which tasks the kill interrupts does not depend on timing."""
            others = scratch.find("run_tasks", {"run_id": run_id, "task_id": {"$ne": KILLED_TASK}})
            return all(t["status"] in {"pending", "done", "skipped"} for t in others)

        _until("the stalled task's siblings to finish", siblings_settled, timeout=30)
        mid = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
        assert mid[KILLED_TASK]["status"] == "running", "the task is running when the worker dies"
        assert mid[KILLED_TASK]["lease_token"], "and it holds a lease that nobody will renew"
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        assert first.returncode == -signal.SIGKILL

        killed_at = time.monotonic()
        second = _spawn(scratch, tmp_path, stage)

        def completed() -> bool:
            run = scratch.find_one("onboarding_runs", {"_id": run_id})
            return run is not None and run["status"] in {"completed", "failed", "blocked"}

        _until("the restarted worker to finish the run", completed, timeout=90)
        recovered_in = time.monotonic() - killed_at
        run = scratch.find_one("onboarding_runs", {"_id": run_id}) or {}
        if run["status"] != "completed":
            second.kill()
            _, errors = second.communicate(timeout=10)
            tasks_now = {
                t["task_id"]: (t["status"], t.get("error")) for t in scratch.find("run_tasks", {"run_id": run_id})
            }
            pytest.fail(f"the run ended {run['status']}: {tasks_now}\n{errors.decode(errors='replace')[-3000:]}")
        assert recovered_in < 60

        second.send_signal(signal.SIGTERM)  # graceful drain: nothing in hand, so it exits at once and cleanly
        assert second.wait(timeout=30) == 0
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    tasks = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
    assert {t["status"] for t in tasks.values()} <= {"done", "skipped"}
    calls = Counter(line.split()[0] for line in _lines(tmp_path, "calls.log"))
    applied = Counter(_lines(tmp_path, "applied.log"))
    ran = {task_id for task_id, t in tasks.items() if t["status"] == "done"}

    # The counter: no task's side effect ran twice, and every task that finished has its effect once.
    assert all(count == 1 for count in applied.values()), f"an effect ran twice: {applied}"
    assert set(applied) == {f"{run_id}:{task_id}" for task_id in ran}
    # Tasks finished before the kill were not run again; the killed one was taken over and run again.
    assert {task_id: n for task_id, n in calls.items() if task_id != KILLED_TASK} == {
        task_id: 1 for task_id in ran if task_id != KILLED_TASK
    }
    assert calls[KILLED_TASK] == 2
    keys = {line.split()[1] for line in _lines(tmp_path, "calls.log") if line.startswith(f"{KILLED_TASK} ")}
    assert keys == {f"{run_id}:{KILLED_TASK}"}, "both attempts carried the same idempotency key"
    assert tasks[KILLED_TASK]["attempts"] == 2
    assert any(h.get("note") == "takeover" for h in tasks[KILLED_TASK]["history"])
    duplicates = _lines(tmp_path, "deduplicated.log")
    assert duplicates == ([f"{run_id}:{KILLED_TASK}"] if stage == "after" else [])
    assert scratch.count("run_checkpoints", {"thread_id": f"{ORG}:{run_id}"}) == 0, "a finished run keeps no checkpoint"
    [job] = scratch.find("jobs", {"run_id": run_id, "consumer": "orchestrator.start_run"})
    assert job["status"] == "done"
    assert scratch.count("dead_letters") == 0
