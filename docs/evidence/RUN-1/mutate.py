"""RUN-1 mutation checks: break each guard on purpose, run the run tests, record which named tests fail.

    uv run --frozen python docs/evidence/RUN-1/mutate.py > docs/evidence/RUN-1/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the RUN-1 unit test files, records
the failing test ids, then restores the file byte for byte. The run ends by checking that no source
file differs from before.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNS = REPO / "libs/py-agents/src/onb_agents/runs"
ORCH = REPO / "apps/orchestrator/src/onb_orchestrator"
API = REPO / "apps/api/src/onb_api/runs"
TESTS = [
    "libs/py-agents/tests/test_run_states.py",
    "libs/py-agents/tests/test_run_records.py",
    "libs/py-agents/tests/test_run_store.py",
    "apps/orchestrator/tests/test_run_graph.py",
    "apps/orchestrator/tests/test_run_failures.py",
    "apps/orchestrator/tests/test_checkpointer.py",
    "apps/api/tests/test_runs_route.py",
    "apps/api/tests/test_role_matrix.py",
]

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    (
        "M1",
        "a join is two plain edges, not a fan-in (box 1)",
        ORCH / "graph.py",
        '            builder.add_edge(depends_on, task["id"])',
        '            for dependency in depends_on:\n                builder.add_edge(dependency, task["id"])',
    ),
    (
        "M2",
        "tasks run one at a time, never in parallel (box 1)",
        ORCH / "graph.py",
        '        "recursion_limit": len(tasks) + _STEP_MARGIN,',
        '        "recursion_limit": len(tasks) + _STEP_MARGIN,\n        "max_concurrency": 1,',
    ),
    (
        "M3",
        "a skipped dependency blocks its dependents (box 2)",
        RUNS / "states.py",
        "_FINISHED: Final = frozenset({TaskState.DONE, TaskState.SKIPPED})",
        "_FINISHED: Final = frozenset({TaskState.DONE})",
    ),
    (
        "M4",
        "the runner no longer re-checks dependencies before readying a task (box 2)",
        ORCH / "runner.py",
        '            if not dependencies_met(task["depends_on"], states):',
        "            if False:",
    ),
    (
        "M5",
        "pending → running is allowed (box 2)",
        RUNS / "states.py",
        "    TaskState.PENDING: frozenset({TaskState.READY, TaskState.SKIPPED, TaskState.CANCELLED}),",
        "    TaskState.PENDING: frozenset({TaskState.READY, TaskState.RUNNING, TaskState.SKIPPED, TaskState.CANCELLED}),",
    ),
    (
        "M6",
        "a done task may run again (box 2)",
        RUNS / "states.py",
        "    TaskState.DONE: frozenset(),",
        "    TaskState.DONE: frozenset({TaskState.RUNNING}),",
    ),
    (
        "M7",
        "a task move is not compare-and-set on the status it was decided on (box 2)",
        RUNS / "store.py",
        '{"run_id": run_id, "task_id": task_id, "status": current["status"]}',
        '{"run_id": run_id, "task_id": task_id}',
    ),
    (
        "M8",
        "a skipped task is executed (box 2)",
        ORCH / "runner.py",
        "        if status in (TaskState.DONE, TaskState.SKIPPED):",
        "        if status is TaskState.DONE:",
    ),
    (
        "M9",
        "the checkpoint thread is the bare run id (box 3)",
        ORCH / "graph.py",
        '"configurable": {"thread_id": thread_id(current_tenant().org_id, run_id)},',
        '"configurable": {"thread_id": run_id},',
    ),
    (
        "M10",
        "the checkpointer accepts another org's thread (box 3)",
        ORCH / "checkpointer.py",
        "    if separator != _SEPARATOR or org != org_id or not run_id or _SEPARATOR in run_id:",
        "    if separator != _SEPARATOR or not run_id or _SEPARATOR in run_id:",
    ),
    (
        "M11",
        "a replayed regular checkpoint write overwrites the first (box 3)",
        ORCH / "checkpointer.py",
        'else "$setOnInsert"',
        'else "$set"',
    ),
    (
        "M12",
        "the run is started on version 1 whatever version was asked for (box 4)",
        API / "service.py",
        "            record = await self._templates.get_version(key, version)",
        "            record = await self._templates.get_version(key, 1)",
    ),
    (
        "M13",
        "the DAG hash is not recomputed before persisting (box 4, ADR 0008)",
        RUNS / "records.py",
        "    _hash_checked(document, dag.hash)\n",
        "    pass\n",
    ),
    (
        "M14",
        "a stored DAG is executed without checking its hash (box 4)",
        RUNS / "records.py",
        '    _hash_checked(document, run.get("dag_hash"))\n',
        "    pass\n",
    ),
    (
        "M15",
        "the consumer drives runs in states it does not own",
        ORCH / "consumer.py",
        '        if run.get("status") not in _DRIVEN:',
        '        if run.get("status") in ("completed", "failed"):',
    ),
    (
        "M16",
        "a manager may start a run (box 5 route guard)",
        API / "routes.py",
        "Depends(require_role(Role.HR, Role.HR_ADMIN))",
        "Depends(require_role(Role.HR, Role.HR_ADMIN, Role.MANAGER))",
    ),
    (
        "M17",
        "a failed run leaves its unfinished tasks (a cut-off sibling stays running) (review HIGH)",
        ORCH / "consumer.py",
        "        await runner.runs.cancel_unfinished(run_id, at=runner.clock())",
        "        pass",
    ),
    (
        "M18",
        "a task another delivery is running is executed again (review)",
        ORCH / "runner.py",
        '            raise TaskInProgressError("another delivery is running this task")',
        "            pass",
    ),
    (
        "M19",
        "a deterministic error is retried instead of failing the run (review MEDIUM)",
        ORCH / "consumer.py",
        "            await _fail(runner, run_id, type(error).__name__)\n            return",
        "            raise",
    ),
    (
        "M20",
        "checkpoints load with the permissive msgpack hook (security MEDIUM)",
        ORCH / "checkpointer.py",
        "JsonPlusSerializer(allowed_msgpack_modules=None)",
        "JsonPlusSerializer(allowed_msgpack_modules=True)",
    ),
    (
        "M21",
        "a node error reaches the checkpoint with its message (review LOW)",
        ORCH / "graph.py",
        "            raise step_error(error) from None",
        "            raise",
    ),
]


def _run_tests() -> tuple[int, list[str]]:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *TESTS],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    failed = sorted({line.split(" ")[1] for line in result.stdout.splitlines() if line.startswith("FAILED ")})
    return result.returncode, failed


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
        print(f"\n{mid} {verdict}: {what}\n  file: {path.relative_to(REPO)}\n  failing tests ({len(failed)}):")
        for test in failed[:8]:
            print(f"    {test}")
        if len(failed) > 8:
            print(f"    ... and {len(failed) - 8} more")
    differing = [str(p.relative_to(REPO)) for p, data in originals.items() if p.read_bytes() != data]
    print(f"\nsource diff after all mutations: {len(differing)} files differ {differing}")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 0 if not survivors and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
