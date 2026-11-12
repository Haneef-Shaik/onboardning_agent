"""RUN-2 mutation checks: break each guard on purpose, run the tests, record which named tests fail.

    uv run --frozen python docs/evidence/RUN-2/mutate.py > docs/evidence/RUN-2/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the unit test files that cover run
control, records the failing test ids, then restores the file byte for byte. The run ends by checking
that no source file differs from before. (The SIGKILL test is an integration test and has its own
mutation, M21, run by hand against the compose stack: see the handoff.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNS = REPO / "libs/py-agents/src/onb_agents/runs"
ORCH = REPO / "apps/orchestrator/src/onb_orchestrator"
API = REPO / "apps/api/src/onb_api/runs"
BUS = REPO / "libs/py-eventbus/src/onb_eventbus"
CONTRACTS = REPO / "libs/py-contracts/src/onb_contracts"
TESTS_BY_PROJECT = [  # one pytest call per project: their `tests` packages clash in a single run (RUN-3)
    [
        "libs/py-agents/tests/test_run_states.py",
        "libs/py-agents/tests/test_run_store.py",
        "libs/py-agents/tests/test_run_control_store.py",
    ],
    ["libs/py-eventbus/tests/test_worker.py"],
    ["libs/py-contracts/tests/test_run_control_contracts.py"],
    [
        "apps/orchestrator/tests/test_run_control.py",
        "apps/orchestrator/tests/test_run_graph.py",
        "apps/orchestrator/tests/test_run_failures.py",
        "apps/orchestrator/tests/test_checkpointer.py",
        "apps/orchestrator/tests/test_worker_main.py",
        "apps/orchestrator/tests/test_sink_rejected.py",
        "apps/orchestrator/tests/test_agent_executor.py",
        "apps/orchestrator/tests/test_run_spans.py",
    ],
    ["apps/api/tests/test_run_control_routes.py", "apps/api/tests/test_role_matrix.py"],
]

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    (
        "M1",
        "a task whose holder died is never taken over (box 1)",
        ORCH / "runner.py",
        "        if not lease_expired(task, now):",
        "        if True:",
    ),
    (
        "M2",
        "a running task may be finished by a worker that no longer holds its lease (box 1)",
        RUNS / "store.py",
        '            guard["lease_token"] = current["lease_token"]',
        "            pass",
    ),
    (
        "M3",
        "a task's half-written checkpoint writes are applied (box 1, found by the SIGKILL test)",
        ORCH / "checkpointer.py",
        '    return [w for w in writes if stored[w["task_id"]] >= int(w.get("count", 0))]',
        "    return list(writes)",
    ),
    (
        "M4",
        "a task gets one attempt more than its maximum (box 2)",
        ORCH / "runner.py",
        "            last = not failure.retryable or attempts >= self.policy.max_attempts",
        "            last = not failure.retryable or attempts > self.policy.max_attempts",
    ),
    (
        "M5",
        "a blocked run tells nobody: `run.blocked` is not published (box 2)",
        ORCH / "consumer.py",
        '                RUN_BLOCKED, {"run_id": run_id, "task_id": task_id}',
        '                "run.unblocked", {"run_id": run_id, "task_id": task_id}',
    ),
    (
        "M6",
        "completing a task by hand needs no reason (box 3)",
        CONTRACTS / "runs.py",
        '    """`POST /runs/{id}/tasks/{task_id}/complete`: HR says the task is done and why (FR-R5)."""\n\n    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)]',
        '    """`POST /runs/{id}/tasks/{task_id}/complete`: HR says the task is done and why (FR-R5)."""\n\n    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)] | None = None',
    ),
    (
        "M7",
        "a manual completion is not audited (box 3)",
        API / "control.py",
        '            await self._audit(principal, f"run.task.{verb}", task_target(run_id, task_id), reason)',
        "            pass",
    ),
    (
        "M8",
        "a manager lists every run of the org (box 4)",
        API / "control.py",
        "            hire_ids = await self._hires.store.ids(narrowing)",
        "            hire_ids = None",
    ),
    (
        "M9",
        "a manager reads a run of someone else's report (box 4)",
        API / "control.py",
        "            require_in_manager_scope(principal, hire)",
        "            pass",
    ),
    (
        "M10",
        "the stream sends only a task's latest transition, not each one (box 5)",
        API / "stream.py",
        "history[seen:]",
        "history[-1:]",
    ),
    (
        "M11",
        "a run that was never paused can be 'resumed' (found by the route tests)",
        API / "control.py",
        '        if target is RunState.RUNNING and run["status"] != RunState.PAUSED.value:',
        "        if False:",
    ),
    (
        "M12",
        "a paused or cancelled run keeps starting tasks",
        ORCH / "runner.py",
        '        if run is None or run.get("status") != RunState.RUNNING.value:',
        "        if run is None:",
    ),
    (
        "M13",
        "a draining worker starts new tasks",
        ORCH / "runner.py",
        "        if self._drain is not None and self._drain.is_set():",
        "        if False:",
    ),
    (
        "M14",
        "a released job costs an attempt (graceful drain)",
        BUS / "documents.py",
        '        "$inc": {"attempts": -1},',
        '        "$inc": {"attempts": 0},',
    ),
    (
        "M15",
        "a cancelled run keeps its checkpoints",
        ORCH / "checkpointer.py",
        '        await self._checkpoints.delete_many({"thread_id": thread})',
        "        pass",
    ),
    (
        "M16",
        "a cut-off task keeps the attempt it did not finish",
        ORCH / "runner.py",
        '                await asyncio.shield(self._release(run_id, task_id, token, int(task["attempts"]) - 1))\n                raise\n            except PyMongoError:',
        '                await asyncio.shield(self._release(run_id, task_id, token, int(task["attempts"])))\n                raise\n            except PyMongoError:',
    ),
    (
        "M17",
        "a takeover is not an attempt, so a task that kills its workers is never blocked",
        RUNS / "store.py",
        '            "attempts": int(expected.get("attempts", 0)) + 1,\n            "started_at": at,',
        '            "attempts": int(expected.get("attempts", 0)),\n            "started_at": at,',
    ),
    (
        "M18",
        "a task that keeps crashing its workers is taken over for ever",
        ORCH / "runner.py",
        '        if int(task["attempts"]) >= self.policy.max_attempts:\n            await self.runs.move_task(',
        '        if False:\n            await self.runs.move_task(',
    ),
    (
        "M19",
        "a manager may control runs",
        API / "routes.py",
        "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]",
        "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN, Role.MANAGER))]",
    ),
    (
        "M20",
        "a running task's lease is never renewed, so a live task is taken over",
        ORCH / "runner.py",
        "            await asyncio.sleep(self.policy.heartbeat_seconds)",
        "            await asyncio.sleep(3600)",
    ),
    (
        "M21",
        "a retried task keeps the previous attempt's cause (RUN-3's stale `failure`)",
        ORCH / "runner.py",
        '"error": None, "retry_at": None, "failure": None},',
        '"error": None, "retry_at": None},',
    ),
    (
        "M22",
        "an executor that says retrying cannot help is retried anyway",
        ORCH / "runner.py",
        "            last = not failure.retryable or attempts >= self.policy.max_attempts",
        "            last = attempts >= self.policy.max_attempts",
    ),
    (
        "M23",
        "a database error from the executor fails the task instead of retrying the job",
        ORCH / "runner.py",
        "            except PyMongoError:\n                # A database error is not the task's failure",
        "            except ZeroDivisionError:\n                # A database error is not the task's failure",
    ),
    (
        "M24",
        "a task failed by a sink is not an attempt: the driver's conflict is an error",
        ORCH / "runner.py",
        "                    if outside is None or outside[\"status\"] != TaskState.FAILED.value:\n                        raise\n                    failure = _Failure(_outside_failure(outside))",
        "                    raise",
    ),
]


def _run_tests() -> tuple[int, list[str]]:
    failed: list[str] = []
    code = 0
    for files in TESTS_BY_PROJECT:
        result = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "-rf", "-x", *files],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        failed += sorted({line.split(" ")[1] for line in result.stdout.splitlines() if line.startswith("FAILED ")})
        code = code or result.returncode
        if failed:
            break  # killed: the first failing project is enough
    return code, failed


def main() -> int:
    originals = {path: path.read_bytes() for _, _, path, _, _ in MUTATIONS}
    code, failed = _run_tests()
    print(f"baseline: exit={code}, failed={len(failed)}")
    if code != 0:
        print("baseline is not green; stopping")
        return 1
    survivors: list[str] = []
    for mid, what, path, old, new in MUTATIONS:
        text = originals[path].decode()
        assert text.count(old) == 1, f"{mid}: snippet must occur exactly once in {path}"
        path.write_text(text.replace(old, new))
        try:
            code, failed = _run_tests()
        finally:
            path.write_bytes(originals[path])
        verdict = "KILLED" if failed else "SURVIVED"
        if not failed:
            survivors.append(mid)
        print(f"\n{mid} {verdict}: {what}\n  file: {path.relative_to(REPO)}\n  failing test: {failed[:1]}", flush=True)
    differing = [str(p.relative_to(REPO)) for p, data in originals.items() if p.read_bytes() != data]
    print(f"\nsource diff after all mutations: {len(differing)} files differ {differing}")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 0 if not survivors and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
