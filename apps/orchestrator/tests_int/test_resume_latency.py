"""HIL-1 box 3: decision → run resumed is under 5 s at p95 over 30+ decisions, on the compose stack.

The orchestrator worker is the production one (`prepare`, default queue settings: a 1 s poll), running in the
background against the compose Mongo replica set. For each decision the clock starts when the approver acts
(the web decision's `decide` call, or the Telegram press's `decision.received` publish, which has one more bus
hop) and stops when the task after the approval **starts**: the run has resumed. Both clocks are this
process's wall clock (the task's `started_at` is stamped by the runner).

Set `HIL1_EVIDENCE_DIR` to write the measurement as JSON (`docs/evidence/HIL-1/`); the assertion holds either way.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from onb_contracts.notify import Decision
from onb_data import tenant_scope

from .conftest import Scratch
from .test_approvals_against_mongo import MANAGER, ORG_A, Stack, build, press

DECISIONS = 36  # half through the web path, half through Telegram
P95_LIMIT_S = 5.0
WAIT_S = 30.0


def _hardware() -> dict[str, Any]:
    """Where the measurement ran (MASTER-PLAN §7): the host and the Docker engine that holds the compose Mongo."""

    def sysctl(name: str) -> str | None:
        try:
            out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    def docker(field: str) -> str | None:
        try:
            out = subprocess.run(
                ["docker", "info", "--format", field], capture_output=True, text=True, check=False, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    memory = sysctl("hw.memsize")
    docker_memory = docker("{{.MemTotal}}")
    return {
        "host": platform.platform(),
        "machine": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "ram_gib": round(int(memory) / 2**30, 1) if memory and memory.isdigit() else None,
        "docker_cpus": docker("{{.NCPU}}"),
        "docker_ram_gib": round(int(docker_memory) / 2**30, 1) if docker_memory and docker_memory.isdigit() else None,
        "docker_server": docker("{{.ServerVersion}}"),
    }


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(q * len(ordered)) - 1))  # nearest-rank
    return ordered[rank]


async def _wait_for(check: Any, what: str) -> Any:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


async def _one(stack: Stack, channel: str, n: int) -> float:
    run_id = await stack.start_run()

    async def waiting() -> dict[str, Any] | None:
        with tenant_scope(ORG_A):
            task = await stack.store.tenant("run_tasks").find_one({"run_id": run_id, "task_id": "manager_approval"})
        return task if task is not None and task["status"] == "waiting_approval" else None

    await _wait_for(waiting, "the approval task to pause")
    approval = await stack.approval(run_id)
    t0 = time.time()
    if channel == "web":
        with tenant_scope(ORG_A):
            await stack.decider().decide(
                approval["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id=MANAGER
            )
    else:
        await press(stack, approval, Decision.APPROVE, query_id=f"cq_latency_{n}")

    async def resumed() -> dict[str, Any] | None:
        with tenant_scope(ORG_A):
            task = await stack.store.tenant("run_tasks").find_one({"run_id": run_id, "task_id": "b"})
        return task if task is not None and task.get("started_at") is not None else None

    after = await _wait_for(resumed, "the run to resume")
    started: datetime = after["started_at"]
    return started.timestamp() - t0


def test_decision_to_resume_is_under_5_seconds_at_p95_over_30_plus_decisions(scratch: Scratch) -> None:
    async def body() -> dict[str, list[float]]:
        stack = await build(scratch)
        stop = asyncio.Event()
        worker = asyncio.create_task(stack.worker.run(stop))
        samples: dict[str, list[float]] = {"web": [], "telegram": []}
        try:
            for n in range(DECISIONS):
                channel = "web" if n % 2 == 0 else "telegram"
                samples[channel].append(await _one(stack, channel, n))
        finally:
            stop.set()
            await asyncio.wait_for(worker, 30)
            await stack.store.close()
        return samples

    samples = asyncio.run(body())
    every = [s for per_channel in samples.values() for s in per_channel]
    assert len(every) == DECISIONS >= 30
    p95 = round(_percentile(every, 0.95), 3)
    report: dict[str, Any] = {
        "goal": "HIL-1",
        "measure": "decision (web decide call / Telegram press publish) to the next task started, seconds",
        "stack": "compose mongo replica set; orchestrator worker in-process (`prepare`, poll 1 s), stub executor",
        "hardware": _hardware(),
        "decisions": len(every),
        "p50_s": round(statistics.median(every), 3),
        "p95_s": p95,
        "max_s": round(max(every), 3),
        "limit_p95_s": P95_LIMIT_S,
        "by_channel": {
            channel: {
                "decisions": len(values),
                "p50_s": round(statistics.median(values), 3),
                "p95_s": round(_percentile(values, 0.95), 3),
                "max_s": round(max(values), 3),
            }
            for channel, values in samples.items()
        },
        "samples_s": {channel: [round(v, 3) for v in values] for channel, values in samples.items()},
    }
    evidence = os.environ.get("HIL1_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        (Path(evidence) / "resume-latency.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "samples_s"}, indent=2))
    assert p95 < P95_LIMIT_S, report
