"""RUN-3 mutation checks: break each guard on purpose, run the RUN-3 tests, record which tests fail.

    uv run --frozen python docs/evidence/RUN-3/mutate.py > docs/evidence/RUN-3/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the RUN-3 unit test files, records
the failing test ids, then restores the file byte for byte. The run ends by checking that no source
file differs from before. (Pattern of docs/evidence/RUN-1/mutate.py.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "apps/orchestrator/src/onb_orchestrator"
AGENTS = REPO / "libs/py-agents/src/onb_agents"
CONTRACTS = REPO / "libs/py-contracts/src/onb_contracts"
EXECUTOR = ORCH / "agent_executor.py"
TESTS = [
    "apps/orchestrator/tests/test_agent_executor.py",
    "apps/orchestrator/tests/test_run_spans.py",
    "apps/orchestrator/tests/test_sink_rejected.py",
    "apps/orchestrator/tests/test_agents_setup.py",
    "apps/orchestrator/tests/test_worker_main.py",
    "apps/orchestrator/tests/test_run_graph.py",
    "apps/orchestrator/tests/test_run_failures.py",
]
# The run-store test lives in py-agents, whose test directory is a different package: a second pytest run.
LIB_TESTS = ["libs/py-agents/tests/test_run_store.py"]
CONTRACT_TESTS = ["libs/py-contracts/tests/test_notify_contracts.py"]

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    (
        "M1",
        "every task gets a grant, declared or not (box 1)",
        EXECUTOR,
        '        access = template_task.get("pii_access")\n        if not access:\n            return None',
        '        access = template_task.get("pii_access") or {"fields": ["mobile"], "sink": "notify.telegram"}',
    ),
    (
        "M2",
        "a task that declares pii_access gets no grant (box 1)",
        EXECUTOR,
        '        access = template_task.get("pii_access")\n        if not access:\n            return None',
        "        access = None\n        if not access:\n            return None",
    ),
    (
        "M3",
        "the grant names another sink than the task declared (box 1)",
        EXECUTOR,
        '                sink=access["sink"],',
        '                sink="notify.email",',
    ),
    (
        "M4",
        "the grant is for the task id, not the run's own run_id (NOTIF-1 wrong_run)",
        EXECUTOR,
        "                run_id=work.run_id,\n                task_id=work.task_id,\n                subject_id=hire_id,",
        "                run_id=work.task_id,\n                task_id=work.task_id,\n                subject_id=hire_id,",
    ),
    (
        "M5",
        "a refused grant does not fail the task: the agent runs without one (box 1)",
        EXECUTOR,
        '            raise AgentTaskFailedError("grant_refused", type(error).__name__) from None',
        "            return None",
    ),
    (
        "M6",
        "no grant issuer configured: pii_access is silently ignored instead of failing closed (box 1)",
        EXECUTOR,
        '            raise AgentTaskFailedError("grant_refused", "no_grant_issuer")',
        "            return None",
    ),
    (
        "M7",
        "the model is not recorded on the task (box 2)",
        EXECUTOR,
        "        stored = await self._runs.record_model(task.run_id, task.task_id, choice.record)",
        "        stored = dict(choice.record)",
    ),
    (
        "M8",
        "a new attempt re-resolves the model instead of keeping the recorded one (box 2)",
        EXECUTOR,
        "        if recorded:\n            return self._models.pinned(manifest, recorded)",
        "        if False:\n            return self._models.pinned(manifest, recorded)",
    ),
    (
        "M9",
        "the recorded model is overwritten on every attempt (box 2)",
        AGENTS / "runs/store.py",
        '{"run_id": run_id, "task_id": task_id, "llm": {"$exists": False}}',
        '{"run_id": run_id, "task_id": task_id}',
    ),
    (
        "M10",
        "each task gets its own fresh run budget: tasks running together do not share the limit (box 3)",
        EXECUTOR,
        "        budget = self._budgets.setdefault(key, created)",
        "        budget = created",
    ),
    (
        "M11",
        "the run budget is not passed to the agent runtime (box 3)",
        EXECUTOR,
        "            result = await runner.run(registered, task, run_budget=budget)",
        "            result = await runner.run(registered, task)",
    ),
    (
        "M12",
        "a restarted worker forgets the tokens the run's tasks already used (box 3)",
        EXECUTOR,
        "        used = await self._runs.run_tokens(run_id)",
        "        used = 0",
    ),
    (
        "M13",
        "a failed agent outcome does not fail the task (box 3)",
        EXECUTOR,
        "            raise AgentTaskFailedError(result.failure.kind, result.failure.reason)",
        "            return",
    ),
    (
        "M14",
        "the failure cause is not stored on the task (box 3)",
        EXECUTOR,
        '        failure = {"kind": result.failure.kind, "reason": result.failure.reason} if result.failure else None',
        "        failure = None",
    ),
    (
        "M15",
        "a retired agent still runs",
        EXECUTOR,
        "        if not found.active:\n            raise AgentTaskFailedError",
        "        if False:\n            raise AgentTaskFailedError",
    ),
    (
        "M16",
        "no run span: tasks hang off the request trace directly (box 4)",
        ORCH / "consumer.py",
        '            with continue_from(run.get("traceparent"), "orchestrator.run", attributes=attributes):',
        '            with continue_from(run.get("traceparent"), "orchestrator.other", attributes=attributes):',
    ),
    (
        "M17",
        "the task span ignores the run span (box 4)",
        ORCH / "runner.py",
        "    if trace.get_current_span().get_span_context().is_valid:",
        "    if False:",
    ),
    (
        "M18",
        "no agent span (box 4)",
        AGENTS / "runtime/runner.py",
        'AGENT_SPAN: Final = "agent.run"',
        'AGENT_SPAN: Final = "agent.other"',
    ),
    (
        "M19",
        "no tool span (box 4)",
        AGENTS / "runtime/graph.py",
        'TOOL_SPAN: Final = "tool.call"',
        'TOOL_SPAN: Final = "tool.other"',
    ),
    (
        "M20",
        "the fail_task consumer fails a task that already finished",
        AGENTS / "runs/store.py",
        '            {"run_id": run_id, "task_id": task_id, "status": TaskState.RUNNING.value},\n            {"$set": {"status"',
        '            {"run_id": run_id, "task_id": task_id},\n            {"$set": {"status"',
    ),
    (
        "M21",
        "a sink's rejection is not applied: the task stays running",
        ORCH / "consumer.py",
        "    return await runs.fail_running(",
        "    return False\n    return await runs.fail_running(",
    ),
    (
        "M22",
        "an agent's late success turns a task failed by a sink `done` again",
        ORCH / "runner.py",
        '            if current is not None and current["status"] == TaskState.FAILED.value:',
        "            if False:",
    ),
    (
        "M23",
        "an agent's outcome is written onto a task that is no longer running (overwrites a sink's cause)",
        AGENTS / "runs/store.py",
        '            match["status"] = only_if.value',
        "            pass",
    ),
    (
        "M24",
        "a rejection without a task id fails every running task, whatever the agent",
        ORCH / "consumer.py",
        '                if t["status"] == TaskState.RUNNING.value and agent in (None, t["agent"])',
        '                if t["status"] == TaskState.RUNNING.value',
    ),
    (
        "M25",
        "the run bus no longer subscribes the fail_task consumer",
        AGENTS / "runs/events.py",
        "    SINK_REJECTED: (FAIL_TASK,),\n",
        "",
    ),
    (
        "M26",
        "the sink's bus does not subscribe the fail_task consumer (no job is written)",
        CONTRACTS / "notify.py",
        "    SINK_REJECTED: [SINK_REJECTED_CONSUMER],\n",
        "",
    ),
    (
        "M27",
        "the worker does not run the fail_task consumer",
        ORCH / "worker.py",
        "        FAIL_TASK: fail_task_handler(store),\n",
        "",
    ),
    (
        "M28",
        "an unknown ORCHESTRATOR_EXECUTOR value silently runs the stub",
        ORCH / "worker.py",
        "        raise ConfigError(\"ORCHESTRATOR_EXECUTOR must be 'stub' or 'agents'\")",
        "        return None",
    ),
]


def _pytest(paths: list[str]) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    failed = [line.split(" ")[1] for line in result.stdout.splitlines() if line.startswith("FAILED ")]
    return result.returncode, failed


def _run_tests() -> tuple[int, list[str]]:
    code, failed = _pytest(TESTS)
    lib_code, lib_failed = _pytest(LIB_TESTS)
    contract_code, contract_failed = _pytest(CONTRACT_TESTS)
    return code or lib_code or contract_code, sorted({*failed, *lib_failed, *contract_failed})


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
        for test in failed[:6]:
            print(f"    {test}")
        if len(failed) > 6:
            print(f"    ... and {len(failed) - 6} more")
    differing = [str(p.relative_to(REPO)) for p, data in originals.items() if p.read_bytes() != data]
    print(f"\nsource diff after all mutations: {len(differing)} files differ {differing}")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 0 if not survivors and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
