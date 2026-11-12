"""HIL-2 mutation checks: break each guard on purpose, run the HIL-2 tests, record which tests fail.

    uv run --frozen --env-file .env python docs/evidence/HIL-2/mutate.py > docs/evidence/HIL-2/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the HIL-2 test files of each project (their
test directories are different packages, so one pytest run per project; the last suite is the Mongo integration
test, which needs the compose stack and `.env`), records the failing test ids, then restores the file byte for
byte. The run ends by checking that no source file differs from before. (Pattern of docs/evidence/HIL-1/mutate.py.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORCH = REPO / "apps/orchestrator/src/onb_orchestrator"
AGENTS = REPO / "libs/py-agents/src/onb_agents"
MOCKS = REPO / "apps/mcp-mocks/src/onb_mcp_mocks/notify"

SUITES: list[list[str]] = [
    ["apps/orchestrator/tests/test_approval_sla.py", "apps/orchestrator/tests/test_worker_main.py"],
    ["libs/py-agents/tests/test_approval_sla.py", "libs/py-agents/tests/test_approvals.py"],
    [
        "apps/mcp-mocks/tests/test_notify_approval_sla.py",
        "apps/mcp-mocks/tests/test_notify_main.py",
        "apps/mcp-mocks/tests/test_notify_approval.py",
    ],
    ["apps/orchestrator/tests_int/test_approval_sla_against_mongo.py"],
]

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    # --- box 1: one reminder, then an escalation at the second threshold -------------------------------------
    (
        "M1",
        "a stage already stamped is owed again (the record no longer stops a repeat)",
        AGENTS / "approvals/sla.py",
        "if approval.get(stage_field(stage)) is None and age >= sla * policy.threshold(stage)",
        "if age >= sla * policy.threshold(stage)",
    ),
    (
        "M2",
        "a decided or cancelled approval is still owed stages",
        AGENTS / "approvals/sla.py",
        'if approval.get("status") != "pending" or requested_at is None:',
        "if requested_at is None:",
    ),
    (
        "M3",
        "the escalation uses the reminder's threshold (no second threshold)",
        AGENTS / "approvals/sla.py",
        'return self.remind_after_sla if stage == "reminder" else self.escalate_after_sla',
        "return self.remind_after_sla",
    ),
    (
        "M4",
        "the stamp applies to a decided approval (a decision during the pass no longer wins)",
        AGENTS / "approvals/store.py",
        '{"_id": approval_id, "status": "pending", field: None}, {"$set": {field: at}}',
        '{"_id": approval_id, field: None}, {"$set": {field: at}}',
    ),
    (
        "M5",
        "the stamp applies again to a stage already stamped (two workers both send)",
        AGENTS / "approvals/store.py",
        '{"_id": approval_id, "status": "pending", field: None}, {"$set": {field: at}}',
        '{"_id": approval_id, "status": "pending"}, {"$set": {field: at}}',
    ),
    (
        "M6",
        "a lost stamp does not roll the publish back (the event goes out anyway)",
        ORCH / "approval_sla.py",
        "            if not await self._approvals.mark_stage(approval_id, stage, at=now):\n"
        "                raise _StageTakenError\n",
        "            await self._approvals.mark_stage(approval_id, stage, at=now)\n",
    ),
    (
        "M7",
        "the event's key is not fixed per approval and stage (a lost stamp sends again)",
        ORCH / "approval_sla.py",
        'return f"approval:{approval_id}:{stage}"',
        'return f"approval:{approval_id}:{stage}:{secrets.token_hex(4)}"',
    ),
    # --- box 3: no repeat after a restart; the stamp and the event commit together ---------------------------
    (
        "M8",
        "the stamp is written before the publish, outside its transaction (a crash leaves a stamp with no event)",
        ORCH / "approval_sla.py",
        "        try:\n            published = await self._bus.publish(\n",
        "        try:\n            await stamp()\n            published = await self._bus.publish(\n",
    ),
    (
        "M9",
        "the sweep does not stamp at all (only the event's key stops a repeat)",
        ORCH / "approval_sla.py",
        "                change=stamp,\n",
        "                change=None,\n",
    ),
    # --- box 2: thresholds from config -----------------------------------------------------------------------
    (
        "M10",
        "the worker ignores APPROVAL_SLA_CONFIG (always the shipped file)",
        ORCH / "worker.py",
        'Path(env.get("APPROVAL_SLA_CONFIG") or DEFAULT_SLA_CONFIG)',
        "Path(DEFAULT_SLA_CONFIG)",
    ),
    (
        "M11",
        "an approval without an SLA ignores the configured default (a fixed 24 h)",
        AGENTS / "approvals/sla.py",
        "    return policy.default_sla_hours\n",
        "    return 24\n",
    ),
    # --- the notifier ----------------------------------------------------------------------------------------
    (
        "M12",
        "a redelivered reminder messages the approver again",
        MOCKS / "sla.py",
        'if await outbox.find_one({**sent_before, "sla_stage": stage}):',
        "if False:",
    ),
    (
        "M13",
        "the escalation carries decision buttons (HR could press for the approver)",
        MOCKS / "sla.py",
        '_BUTTONS if stage == "reminder" else ()',
        "_BUTTONS",
    ),
    (
        "M14",
        "an approval decided meanwhile is still chased",
        MOCKS / "sla.py",
        'if approval is None or approval.get("status") != "pending":',
        "if approval is None:",
    ),
    (
        "M15",
        "the escalation goes to the approver instead of HR",
        MOCKS / "sla.py",
        "approval_recipients(store, approval) if role is None else _role_users(store, role)",
        "approval_recipients(store, approval)",
    ),
    # --- review fixes ---------------------------------------------------------------------------------------
    (
        "M16",
        "the notifier acts on a stage the sweep never stamped (a forged or early event is delivered)",
        MOCKS / "sla.py",
        "        if approval.get(_STAMPS[stage]) is None:\n",
        "        if False:\n",
    ),
    (
        "M17",
        "in-app notes deduplicate per event, not per stage (each republish adds a note)",
        MOCKS / "sla.py",
        'dedupe_key=f"sla:{approval_id}:{stage}:{user_id}"',
        'dedupe_key=f"{job.event_id}:{user_id}"',
    ),
    (
        "M18",
        "the sweep reads only the first page (a backlog that is not due starves newer approvals)",
        ORCH / "approval_sla.py",
        "            if len(page) < self._batch:\n",
        "            if True:\n",
    ),
    (
        "M19",
        "a duplicate publish stops the stages (a lost stamp blocks the escalation forever)",
        ORCH / "approval_sla.py",
        '        return "already"\n',
        '        return "taken"\n',
    ),
    (
        "M20",
        "the sweep keeps starting passes while the worker drains (it ignores the worker's stop)",
        ORCH / "worker.py",
        "        await stop.wait()\n        sweep_stop.set()\n",
        "        await asyncio.Event().wait()\n",
    ),
    (
        "M21",
        "a stuck sweep is awaited without a bound (shutdown waits forever)",
        ORCH / "worker.py",
        "await asyncio.wait_for(sweeping, timeout=settings.drain_seconds)",
        "await sweeping",
    ),
    (
        "M22",
        "HIL-1's notifier takes a reminder for its own first message (the approver never gets the original)",
        MOCKS / "approvals.py",
        '{**first_message, "sla_stage": {"$exists": False}}',
        "first_message",
    ),
]

# M7 needs `secrets` in the module; the mutation adds the import with it.
_IMPORTS: dict[str, tuple[str, str]] = {"M7": ("import asyncio\n", "import asyncio\nimport secrets\n")}


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


def main() -> int:
    only = set(sys.argv[1:])
    originals = {path: path.read_bytes() for _, _, path, _, _ in MUTATIONS}
    code, failed = _run_tests()
    print(f"baseline: exit={code}, failed={len(failed)} {failed}")
    if code != 0:
        print("baseline is not green; stopping")
        return 1
    survivors: list[str] = []
    for mid, what, path, old, new in MUTATIONS:
        if only and mid not in only:
            continue
        text = originals[path].decode()
        assert text.count(old) == 1, f"{mid}: snippet must occur exactly once in {path}"
        mutated = text.replace(old, new)
        if mid in _IMPORTS:
            before, after = _IMPORTS[mid]
            assert mutated.count(before) == 1
            mutated = mutated.replace(before, after)
        path.write_text(mutated)
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
    total = len(only) if only else len(MUTATIONS)
    print(f"mutations: {total}, killed: {total - len(survivors)}, survivors: {survivors or 'none'}")
    return 0 if not survivors and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
