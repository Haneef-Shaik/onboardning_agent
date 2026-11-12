"""HIL-1 mutation checks: break each guard on purpose, run the HIL-1 unit tests, record which tests fail.

    uv run --frozen python docs/evidence/HIL-1/mutate.py > docs/evidence/HIL-1/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the HIL-1 unit test files of each
project (their test directories are different packages, so one pytest run per project), records the
failing test ids, then restores the file byte for byte. The run ends by checking that no source file differs
from before. (Pattern of docs/evidence/RUN-3/mutate.py.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "apps/orchestrator/src/onb_orchestrator"
AGENTS = REPO / "libs/py-agents/src/onb_agents"
CONTRACTS = REPO / "libs/py-contracts/src/onb_contracts"
MOCKS = REPO / "apps/mcp-mocks/src/onb_mcp_mocks/notify"
API = REPO / "apps/api/src/onb_api/approvals"

# One pytest run per project: test directories are separate packages (`tests`).
SUITES: list[list[str]] = [
    [
        "apps/orchestrator/tests/test_approvals_in_runs.py",
        "apps/orchestrator/tests/test_record_decision.py",
        "apps/orchestrator/tests/test_agent_approvals.py",
        "apps/orchestrator/tests/test_worker_main.py",
        "apps/orchestrator/tests/test_agents_setup.py",
    ],
    [
        "libs/py-agents/tests/test_approvals.py",
        "libs/py-agents/tests/test_run_store.py",
        "libs/py-agents/tests/test_run_states.py",
    ],
    ["libs/py-contracts/tests/test_notify_contracts.py"],
    ["apps/mcp-mocks/tests/test_notify_approval.py", "apps/mcp-mocks/tests/test_notify_main.py"],
    ["apps/api/tests/test_approvals_route.py"],
    # The race guards (M1-M3) only matter under real transactions: the Mongo integration tests (need `.env`).
    ["apps/orchestrator/tests_int/test_approvals_against_mongo.py"],
]

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    # --- the decision write: first decision wins (box 2) ---------------------------------------------------
    (
        "M1",
        "the decision write applies to any approval, not only a pending one (a second decision overwrites)",
        AGENTS / "approvals/store.py",
        '{"_id": approval_id, "status": "pending"}, {"$set": {"status": status, "decision": dict(decision)}}',
        '{"_id": approval_id}, {"$set": {"status": status, "decision": dict(decision)}}',
    ),
    (
        "M2",
        "a lost decision write does not roll the transaction back (the loser publishes its event anyway)",
        AGENTS / "approvals/decide.py",
        "            if not await self._approvals.decide_if_pending(approval_id, stored, status):\n"
        "                raise AlreadyDecidedError(approval_id)\n",
        "            await self._approvals.decide_if_pending(approval_id, stored, status)\n",
    ),
    (
        "M3",
        "a decision whose event key already exists is not refused",
        AGENTS / "approvals/decide.py",
        "        if result.duplicate:\n            raise AlreadyDecidedError(approval_id)\n",
        "",
    ),
    (
        "M4",
        "the pending check before the write is gone and the approver check passes anyone",
        AGENTS / "approvals/decide.py",
        '    if approval.get("approver_id") is not None:\n        return bool(approval["approver_id"] == user_id)\n',
        '    if approval.get("approver_id") is not None:\n        return True\n',
    ),
    (
        "M5",
        "a role approval can be decided by a user of any role",
        AGENTS / "approvals/decide.py",
        '    return role is not None and approval.get("approver_role") == role',
        "    return True",
    ),
    (
        "M6",
        "the decision is not audited",
        AGENTS / "approvals/decide.py",
        "            if self._auditor is not None:  # same transaction",
        "            if False:  # same transaction",
    ),
    (
        "M7",
        "a comment of any length is accepted by the decision write",
        AGENTS / "approvals/decide.py",
        "        if comment is not None and len(comment) > MAX_COMMENT_CHARS:",
        "        if False:",
    ),
    # --- the approval record -------------------------------------------------------------------------------
    (
        "M8",
        "approval ids are not deterministic: a redelivered job opens a second approval",
        AGENTS / "approvals/store.py",
        '        key = "\\x1f".join((self.run_id, self.task_id, self.kind, self.rule or ""))',
        '        key = "\\x1f".join((self.run_id, self.task_id, self.kind, self.rule or "", __import__("secrets").token_hex(4)))',
    ),
    (
        "M9",
        "a later packet overwrites the first",
        AGENTS / "approvals/store.py",
        '{"_id": approval_id, "packet": None, "packet_failure": None}',
        '{"_id": approval_id}',
    ),
    (
        "M10",
        "a pending tool-call approval already grants its rule",
        AGENTS / "approvals/store.py",
        '"kind": "tool_call", "status": "approved"},',
        '"kind": "tool_call"},',
    ),
    (
        "M11",
        "another task's approved rule reaches this task's token",
        AGENTS / "approvals/store.py",
        '{"run_id": run_id, "task_id": task_id, "kind": "tool_call", "status": "approved"},',
        '{"run_id": run_id, "kind": "tool_call", "status": "approved"},',
    ),
    (
        "M12",
        "the approver rule is ignored: a `user` approver falls through to the manager",
        AGENTS / "approvals/approver.py",
        '        if rule == "user" and ref:\n            return ref, None\n',
        "",
    ),
    (
        "M13",
        "a task without an approver rule goes to nobody-specific HR even when the hire has a manager",
        AGENTS / "approvals/approver.py",
        '    manager = _hire_user(hire, "manager_id")',
        "    manager = None",
    ),
    # --- the run engine: pause and resume (boxes 2, 4) -----------------------------------------------------
    (
        "M14",
        "an approval task completes instead of waiting",
        ORCH / "runner.py",
        '            if isinstance(failure, _Asked) or (failure is None and task["type"] == "approval"):',
        "            if isinstance(failure, _Asked):",
    ),
    (
        "M15",
        "a rejection does not fail the task",
        ORCH / "runner.py",
        '        if approval["status"] != "approved":\n            decided',
        "        if False:\n            decided",
    ),
    (
        "M16",
        "an approved tool call completes the task without running it again with the rule approved",
        ORCH / "runner.py",
        '        if approval["kind"] == "tool_call":\n            if again:',
        "        if False:\n            if again:",
    ),
    (
        "M17",
        "a tool call refused again after its approval waits again instead of failing (an approval loop)",
        ORCH / "runner.py",
        '            if again:\n                await self._fail_for_approval(run_id, task, "approval_ineffective"',
        '            if False:\n                await self._fail_for_approval(run_id, task, "approval_ineffective"',
    ),
    (
        "M18",
        "the graph node does not interrupt: the paused task is settled at once",
        ORCH / "graph.py",
        '            interrupt({"approval_id": approval_id})\n',
        "",
    ),
    (
        "M19",
        "settle finishes a task whose approval is still pending",
        ORCH / "runner.py",
        '        if approval["status"] == "pending":\n            raise TaskNotSettledError("the approval is not decided")\n',
        "",
    ),
    (
        "M20",
        "the resumed task counts as another attempt",
        AGENTS / "runs/store.py",
        'if target is TaskState.RUNNING and current["status"] != TaskState.WAITING_APPROVAL.value:',
        "if target is TaskState.RUNNING:",
    ),
    (
        "M21",
        "waiting_approval → running is not a legal move",
        AGENTS / "runs/states.py",
        "TaskState.WAITING_APPROVAL: frozenset({TaskState.RUNNING, TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}),",
        "TaskState.WAITING_APPROVAL: frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}),",
    ),
    (
        "M22",
        "the run finishes (completes) when its graph paused for an approval",
        ORCH / "consumer.py",
        "    if paused:\n        return  # waiting for an approval: the decision resumes it\n",
        "",
    ),
    (
        "M23",
        "the resume handler resumes an approval nobody decided",
        ORCH / "consumer.py",
        '        if approval is None or approval["status"] == "pending":\n            return',
        "        if approval is None:\n            return",
    ),
    (
        "M24",
        "a resume that cannot find the paused graph is dropped instead of retried",
        ORCH / "consumer.py",
        '        raise TaskStepError("InterruptNotCheckpointed", transient=True)  # the start job is still pausing',
        "        return False",
    ),
    (
        "M25",
        "two approvals of one run can be resumed at once (no resume lease)",
        AGENTS / "runs/store.py",
        "        return outcome.matched == 1\n\n    async def release_resume",
        "        return True\n\n    async def release_resume",
    ),
    # --- the Telegram path (box 2) -------------------------------------------------------------------------
    (
        "M26",
        "a press is not tied to the approval its message carries",
        ORCH / "consumer.py",
        'or ref.get("id") != approval_id or not user_id:',
        "or not user_id:",
    ),
    (
        "M27",
        "a press on anything is treated as an approval decision",
        ORCH / "consumer.py",
        '        if payload.get("ref_type") != APPROVAL_REF_TYPE:\n            return\n',
        "",
    ),
    (
        "M28",
        "a Telegram decision is attributed to the web channel",
        ORCH / "consumer.py",
        '                channel="telegram",',
        '                channel="web",',
    ),
    (
        "M29",
        "a Telegram decision is attributed to whoever the event names, not the message's audience",
        ORCH / "consumer.py",
        "                user_id=str(user_id),",
        '                user_id=str(payload.get("user_id") or user_id),',
    ),
    # --- agents (box 4) ------------------------------------------------------------------------------------
    (
        "M30",
        "an approved rule never reaches the agent's token",
        ORCH / "agent_executor.py",
        "                approvals=await self._approvals.approved_rules(work.run_id, work.task_id),",
        "                approvals=(),",
    ),
    (
        "M31",
        "any refusal, not only approval_required, opens an approval",
        ORCH / "agent_executor.py",
        '        if step.kind != "tool" or step.outcome != "denied" or step.reason != APPROVAL_REQUIRED:',
        '        if step.kind != "tool" or step.outcome != "denied":',
    ),
    (
        "M32",
        "a refused call opens an approval for any tool, with no rule in the manifest",
        ORCH / "agent_executor.py",
        "            if rule in candidates:  # the hub's own matching (policy `_check_approval`)",
        "            if True:",
    ),
    (
        "M33",
        "a needs_human answer completes the task",
        ORCH / "agent_executor.py",
        '    if (result.output or {}).get("status") == "needs_human":',
        "    if False:",
    ),
    (
        "M34",
        "the Approvals Agent's packet is not stored",
        ORCH / "agent_executor.py",
        "            await self._store_packet(work, approval_id, result)\n            return",
        "            return",
    ),
    (
        "M35",
        "the agent is not told which approval it summarises",
        ORCH / "agent_executor.py",
        '        return {**asked, "approval_id": approval_id} if approval_id is not None else dict(asked)',
        "        return dict(asked)",
    ),
    # --- notification --------------------------------------------------------------------------------------
    (
        "M36",
        "buttons are sent for an approval that is already decided",
        MOCKS / "approvals.py",
        '        if approval is None or approval.get("status") != "pending":',
        "        if approval is None:",
    ),
    (
        "M37",
        "the Telegram message is not addressed to the approver",
        MOCKS / "approvals.py",
        "            audience_user_id=user_id,",
        "            audience_user_id=None,",
    ),
    (
        "M38",
        "the message text is not redacted",
        MOCKS / "approvals.py",
        '    return redact(_clip("\\n".join(lines), _MAX_TEXT))',
        '    return _clip("\\n".join(lines), _MAX_TEXT)',
    ),
    (
        "M39",
        "the same event delivered again messages the approver again",
        MOCKS / "approvals.py",
        '        if await outbox.find_one({"ref.type": APPROVAL_REF_TYPE, "ref.id": approval_id, "audience_user_id": user_id}):\n            return',
        "        if False:\n            return",
    ),
    # --- the API (boxes 2, 5) ------------------------------------------------------------------------------
    (
        "M40",
        "a second decision is not a 409",
        API / "service.py",
        "            raise AppError(ErrorCode.CONFLICT, _ALREADY_DECIDED) from None",
        "            raise AppError(ErrorCode.NOT_FOUND) from None",
    ),
    (
        "M41",
        "someone who is not the approver is told so (403/500) instead of 404",
        API / "service.py",
        "        except (ApprovalNotFoundError, NotApproverError):",
        "        except ApprovalNotFoundError:",
    ),
    (
        "M42",
        "a manager can list the whole org's approvals",
        API / "service.py",
        "            if principal.role is Role.MANAGER:\n                raise AppError(ErrorCode.FORBIDDEN)",
        "            if False:\n                raise AppError(ErrorCode.FORBIDDEN)",
    ),
    (
        "M43",
        "the web decision is recorded on the telegram channel",
        API / "service.py",
        '                channel="web",',
        '                channel="telegram",',
    ),
    # --- events and consumers ------------------------------------------------------------------------------
    (
        "M44",
        "the run bus does not subscribe the resume consumer",
        AGENTS / "runs/events.py",
        "    APPROVAL_DECIDED: (RESUME_APPROVAL,),\n",
        "",
    ),
    (
        "M45",
        "the webhook's bus does not subscribe the decision recorder",
        CONTRACTS / "notify.py",
        "    DECISION_RECEIVED: [RECORD_DECISION_CONSUMER],\n",
        "",
    ),
    (
        "M46",
        "the worker does not run the resume consumer",
        ORCH / "worker.py",
        "        RESUME_APPROVAL: resume_approval_handler(  # HIL-1: an approval was decided\n            store, tasks, bus=bus, policy=policy, drain=drain\n        ),\n",
        "",
    ),
    (
        "M47",
        "the worker does not run the decision recorder",
        ORCH / "worker.py",
        "        RECORD_DECISION_CONSUMER: record_decision_handler(store, bus),  # HIL-1: a Telegram press\n",
        "",
    ),
    (
        "M48",
        "the notify bus does not subscribe the approvals notifier",
        CONTRACTS / "notify.py",
        "    APPROVAL_REQUESTED: [APPROVAL_NOTIFY_CONSUMER],  # HIL-1: the notifier lives in mcp-mocks\n",
        "",
    ),
    # --- merged with RUN-2 / AGT-1 ----------------------------------------------------------------------------
    (
        "M49",
        "an approved tool call's re-run takes no lease (the task runs unleased)",
        ORCH / "runner.py",
        '                fields={**self._lease(), "error": None, "retry_at": None, "failure": None},\n            )\n            await self._attempts(run_id, running, traceparent)',
        '                fields={"error": None, "retry_at": None, "failure": None},\n            )\n            await self._attempts(run_id, running, traceparent)',
    ),
    (
        "M50",
        "a failed run leaves its pending approvals pending",
        ORCH / "consumer.py",
        "            await runner.approvals.cancel_pending(run_id, at=runner.clock())  # nothing left to decide (HIL-1)\n",
        "",
    ),
    (
        "M51",
        "a cancelled run leaves its pending approvals pending",
        ORCH / "consumer.py",
        "        await approvals.cancel_pending(run_id, at=_utc_now())\n",
        "",
    ),
    (
        "M52",
        "driving a run on does not settle approvals decided while it was paused (it pauses again)",
        ORCH / "consumer.py",
        "        await self._settle_decided(built)\n",
        "",
    ),
    (
        "M53",
        "the agent's inner graph inherits the run graph's checkpoint (a re-run resumes stale state)",
        AGENTS / "runtime/graph.py",
        "    return graph.compile(checkpointer=False)",
        "    return graph.compile()",
    ),
    (
        "M54",
        "a third approval for one task is not refused",
        ORCH / "runner.py",
        "        if f\"{kind}\\x1f{rule or ''}\" not in known and len(existing) >= MAX_APPROVALS_PER_TASK:",
        "        if False:",
    ),
    (
        "M55",
        "a decision while the run is paused is acted on (the resume handler ignores the run state)",
        ORCH / "consumer.py",
        '        if run is None or run.get("status") != RunState.RUNNING.value:\n            return\n        task = await runner.runs.task(',
        "        if run is None:\n            return\n        task = await runner.runs.task(",
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
    if result.returncode != 0 and not failed:  # a collection error or a crash counts as a failure too
        failed = [f"<pytest exit {result.returncode}: {paths[0]}>"]
    return result.returncode, failed


def _run_tests() -> tuple[int, list[str]]:
    code, failed = 0, set[str]()
    for suite in SUITES:
        suite_code, suite_failed = _pytest(suite)
        code = code or suite_code
        failed.update(suite_failed)
    return code, sorted(failed)


# Mutants that cannot change behaviour because another guard holds the same line (documented, not ignored).
EQUIVALENT: dict[str, str] = {
    "M55": "RUN-2's `_ensure_runnable` halts a paused run before the resumed node does anything, so the handler's "
    "own early return changes nothing observable (defence in depth)",
}


def main() -> int:
    only = set(sys.argv[1:])  # optional: re-run only these mutation ids
    originals = {path: path.read_bytes() for _, _, path, _, _ in MUTATIONS}
    code, failed = _run_tests()
    print(f"baseline: exit={code}, failed={len(failed)}")
    if code != 0:
        print("baseline is not green; stopping")
        return 1
    survivors: list[str] = []
    for mid, what, path, old, new in MUTATIONS:
        if only and mid not in only:
            continue
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
    equivalent = [mid for mid in survivors if mid in EQUIVALENT]
    for mid in equivalent:
        print(f"{mid} is an equivalent mutant: {EQUIVALENT[mid]}")
    real = [mid for mid in survivors if mid not in EQUIVALENT]
    total = len(only) if only else len(MUTATIONS)
    print(
        f"mutations: {total}, killed: {total - len(survivors)}, equivalent: {equivalent or 'none'}, survivors: {real or 'none'}"
    )
    return 0 if not real and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
