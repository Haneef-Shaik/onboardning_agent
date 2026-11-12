"""HIL-1 against the compose Mongo replica set: an approval task pauses a run through the real outbox, the
decision (web or Telegram) commits with its event, and a real worker resumes the checkpointed graph (PRD
FR-H1-H3, FR-R6, §7.2).

The worker is the production one (`prepare`): `start_run`, `fail_task`, `resume_run` and `record_decision`
over `run_bus`. Every test has its own scratch database under the T-2 safety net.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from onb_agents.approvals import AlreadyDecidedError, ApprovalDecider, ApprovalStore
from onb_agents.runs import RunStarter, RunStore, run_bus
from onb_agents.runs.testing import hire_variables_for
from onb_contracts.approvals import APPROVAL_DECIDED
from onb_contracts.notify import DECISION_RECEIVED, Decision
from onb_contracts.templates import Template
from onb_data import AuditLog, DataStore, connect, tenant_scope
from onb_eventbus import MongoEventBus, QueueSettings
from onb_mcp_mocks.notify.outbox import DecisionButton, OutboxEntry, TelegramMockChannel
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_orchestrator.worker import RunWorker, prepare

from .conftest import Scratch

ORG_A = "org_acme"
MANAGER = "u_acme_mgr_ravi"
EXTRA_COLLECTIONS = ("approvals", "mock_outbox", "new_hires", "users")


def _task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id.upper()}",
        "agent": "provisioning_agent",
        "action": f"do_{task_id}",
        "depends_on": list(depends_on),
        **extra,
    }


def approval_template() -> Template:
    """a → manager_approval → (b, c): the approval pauses everything after it."""
    approval = _task(
        "manager_approval",
        "a",
        type="approval",
        agent="approvals_agent",
        action="approval",
        approver={"rule": "variable", "ref": "manager_id"},
        sla_hours=48,
    )
    return Template.model_validate(
        {
            "key": "approval-int",
            "name": "Approval (integration)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": [_task("a"), approval, _task("b", "manager_approval"), _task("c", "manager_approval")],
        }
    )


@dataclass
class Stack:
    store: DataStore
    bus: MongoEventBus
    worker: RunWorker

    async def drain(self) -> int:
        processed = 0
        while await self.worker.worker.run_once() is not None:
            processed += 1
        return processed

    async def start_run(self, org: str = ORG_A) -> str:
        starter = RunStarter(RunStore(self.store), self.bus, AuditLog(self.store))
        with tenant_scope(org):
            await self.store.tenant("new_hires").update_one(
                {"_id": "h_1"}, {"$set": {"manager_id": MANAGER}}, upsert=True
            )
            started = await starter.start(
                template=approval_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
            )
        return str(started.run["_id"])

    def decider(self, auditor: Any = None) -> ApprovalDecider:
        return ApprovalDecider(ApprovalStore(self.store), self.bus, auditor=auditor or AuditLog(self.store))

    async def approval(self, run_id: str, org: str = ORG_A) -> dict[str, Any]:
        with tenant_scope(org):
            [found] = await ApprovalStore(self.store).for_task(run_id, "manager_approval")
        return found


async def build(scratch: Scratch) -> Stack:
    existing = set(scratch.db.list_collection_names())
    for name in EXTRA_COLLECTIONS:
        if name not in existing:
            scratch.db.create_collection(name)
    store = connect(scratch.uri, scratch.name)
    bus = run_bus(store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
    worker = await prepare(store, stub_delay=0.0)
    await ApprovalStore(store).ensure_indexes()
    return Stack(store, bus, worker)


def with_stack(scratch: Scratch, body: Callable[[Stack], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        stack = await build(scratch)
        try:
            return await body(stack)
        finally:
            await stack.store.close()

    return asyncio.run(go())


def task_states(scratch: Scratch, run_id: str) -> dict[str, str]:
    return {t["task_id"]: t["status"] for t in scratch.find("run_tasks", {"run_id": run_id})}


async def press(stack: Stack, approval: dict[str, Any], decision: Decision, query_id: str = "cq_1") -> None:
    """A Telegram press on the approval's buttons: the outbox message the notifier would have sent, then the
    `decision.received` event the webhook publishes for it."""
    channel = TelegramMockChannel(stack.store, TelegramMockTransport())
    with tenant_scope(ORG_A):
        users = stack.store.tenant("users")
        if not await users.find_one({"_id": MANAGER}):
            await users.insert_one({"_id": MANAGER, "role": "MANAGER"})
        message = await stack.store.tenant("mock_outbox").find_one({"ref.id": approval["_id"]})
        if message is None:
            entry = OutboxEntry(
                recipient_token=None,
                recipient_masked=f"user:{MANAGER}",
                text="Approval needed",
                run_id=approval["run_id"],
                task_id=approval["task_id"],
                ref={"type": "approval", "id": approval["_id"]},
                audience_user_id=MANAGER,
            )
            buttons = [DecisionButton("Approve", Decision.APPROVE), DecisionButton("Reject", Decision.REJECT)]
            _, message = await channel.send(731957001, entry, buttons=buttons)
            await channel.store(message)
        payload = {
            "ref_type": "approval",
            "ref_id": approval["_id"],
            "decision": decision.value,
            "channel": "telegram",
            "outbox_id": message["_id"],
            "callback_query_id": query_id,
        }
        await stack.bus.publish(DECISION_RECEIVED, payload, idempotency_key=f"telegram:callback:{query_id}")


def test_an_approval_task_pauses_the_run_through_the_real_bus_and_a_web_decision_resumes_it(scratch: Scratch) -> None:
    async def body(stack: Stack) -> tuple[str, dict[str, Any]]:
        run_id = await stack.start_run()
        await stack.drain()
        approval = await stack.approval(run_id)
        with tenant_scope(ORG_A):
            await stack.decider().decide(
                approval["_id"], decision=Decision.APPROVE, comment="ok", channel="web", user_id=MANAGER
            )
        await stack.drain()
        return run_id, approval

    run_id, approval = with_stack(scratch, body)
    assert task_states(scratch, run_id) == {"a": "done", "manager_approval": "done", "b": "done", "c": "done"}
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["status"] == "completed"
    stored = scratch.find_one("approvals", {"_id": approval["_id"]})
    assert stored is not None
    assert (stored["org_id"], stored["status"], stored["decision"]["channel"]) == (ORG_A, "approved", "web")
    events = {e["type"] for e in scratch.find("events", {"run_id": run_id})}
    assert {"run.started", "approval.requested", APPROVAL_DECIDED} <= events
    # Every orchestrator job finished (the notifier's job waits for mcp-notify's worker, not run here).
    jobs = scratch.find("jobs", {"run_id": run_id, "consumer": {"$regex": "^orchestrator\\."}})
    assert jobs
    assert {j["status"] for j in jobs} == {"done"}


def test_the_run_is_paused_not_finished_before_the_decision(scratch: Scratch) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start_run()
        await stack.drain()
        return run_id

    run_id = with_stack(scratch, body)
    assert task_states(scratch, run_id) == {
        "a": "done",
        "manager_approval": "waiting_approval",
        "b": "pending",
        "c": "pending",
    }
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["status"] == "running"
    assert scratch.count("run_checkpoints", {"thread_id": f"{ORG_A}:{run_id}"}) >= 1  # the pause is checkpointed


def test_a_telegram_press_resumes_the_run_and_is_recorded_for_the_approver(scratch: Scratch) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start_run()
        await stack.drain()
        await press(stack, await stack.approval(run_id), Decision.APPROVE)
        await stack.drain()
        return run_id

    run_id = with_stack(scratch, body)
    assert set(task_states(scratch, run_id).values()) == {"done"}
    [stored] = scratch.find("approvals", {"run_id": run_id})
    assert (stored["status"], stored["decision"]["channel"], stored["decision"]["decided_by"]) == (
        "approved",
        "telegram",
        MANAGER,
    )
    [audit] = scratch.find("audit_log", {"action": "approval.decide"})
    assert (audit["actor"]["id"], audit["reason"]) == (MANAGER, "approve:telegram")


def test_a_rejection_through_telegram_fails_the_run(scratch: Scratch) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start_run()
        await stack.drain()
        await press(stack, await stack.approval(run_id), Decision.REJECT)
        await stack.drain()
        return run_id

    run_id = with_stack(scratch, body)
    assert task_states(scratch, run_id) == {
        "a": "done",
        "manager_approval": "failed",
        "b": "cancelled",
        "c": "cancelled",
    }
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["status"] == "failed"


def test_concurrent_web_and_telegram_decisions_record_exactly_one_on_the_real_database(scratch: Scratch) -> None:
    """Box 2 on real transactions: the web decision and the press race; one is recorded, the loser is
    `AlreadyDecidedError` (the API's 409) or a no-op (Telegram), one `approval.decided` event exists, and the
    run resumes once."""

    async def body(stack: Stack) -> tuple[str, list[object]]:
        run_id = await stack.start_run()
        await stack.drain()
        approval = await stack.approval(run_id)

        async def web() -> object:
            with tenant_scope(ORG_A):
                return await stack.decider().decide(
                    approval["_id"], decision=Decision.REJECT, comment="web", channel="web", user_id=MANAGER
                )

        results = await asyncio.gather(web(), press(stack, approval, Decision.APPROVE), return_exceptions=True)
        await stack.drain()
        return run_id, list(results)

    run_id, results = with_stack(scratch, body)
    web_result, press_result = results
    assert press_result is None
    [stored] = scratch.find("approvals", {"run_id": run_id})
    assert len(scratch.find("events", {"type": APPROVAL_DECIDED, "run_id": run_id})) == 1
    assert len(scratch.find("audit_log", {"action": "approval.decide"})) == 1
    if stored["decision"]["channel"] == "web":
        assert isinstance(web_result, dict)
        assert stored["status"] == "rejected"
    else:
        assert isinstance(web_result, AlreadyDecidedError)
        assert stored["status"] == "approved"
    statuses = set(task_states(scratch, run_id).values())
    assert statuses in ({"done"}, {"done", "failed", "cancelled"})  # resumed exactly once, whichever won


def test_many_concurrent_decisions_record_one(scratch: Scratch) -> None:
    async def body(stack: Stack) -> list[object]:
        run_id = await stack.start_run()
        await stack.drain()
        approval = await stack.approval(run_id)

        async def attempt(n: int) -> object:
            with tenant_scope(ORG_A):
                return await stack.decider().decide(
                    approval["_id"],
                    decision=Decision.APPROVE if n % 2 else Decision.REJECT,
                    comment=str(n),
                    channel="web" if n % 3 else "telegram",
                    user_id=MANAGER,
                )

        results = await asyncio.gather(*(attempt(n) for n in range(8)), return_exceptions=True)
        await stack.drain()
        return list(results)

    results = with_stack(scratch, body)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, AlreadyDecidedError) for r in results) == 7
    assert scratch.count("events", {"type": APPROVAL_DECIDED}) == 1


def test_a_decision_whose_audit_write_fails_is_not_made_and_publishes_nothing(scratch: Scratch) -> None:
    class FailingAudit:
        async def audit(self, **_: Any) -> str:
            raise RuntimeError("the audit write failed")

    async def body(stack: Stack) -> dict[str, Any]:
        run_id = await stack.start_run()
        await stack.drain()
        approval = await stack.approval(run_id)
        with tenant_scope(ORG_A):
            try:
                await stack.decider(FailingAudit()).decide(
                    approval["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id=MANAGER
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("the decision went through without its audit record")
        return approval

    approval = with_stack(scratch, body)
    stored = scratch.find_one("approvals", {"_id": approval["_id"]})
    assert stored is not None
    assert (stored["status"], stored["decision"]) == ("pending", None)  # rolled back with its event
    assert scratch.count("events", {"type": APPROVAL_DECIDED}) == 0


def test_the_pause_survives_a_restart_of_the_whole_stack(scratch: Scratch) -> None:
    """FR-R6: the paused run lives in MongoDB (its checkpoint and its task), not in a process."""

    async def first(stack: Stack) -> str:
        run_id = await stack.start_run()
        await stack.drain()
        return run_id

    run_id = with_stack(scratch, first)

    async def second(stack: Stack) -> None:  # a new store, a new bus, a new worker
        approval = await stack.approval(run_id)
        with tenant_scope(ORG_A):
            await stack.decider().decide(
                approval["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id=MANAGER
            )
        await stack.drain()

    with_stack(scratch, second)
    assert set(task_states(scratch, run_id).values()) == {"done"}


def test_another_orgs_decision_cannot_resume_this_orgs_run(scratch: Scratch) -> None:
    async def body(stack: Stack) -> None:
        run_id = await stack.start_run()
        await stack.drain()
        approval = await stack.approval(run_id)
        with tenant_scope("org_globex"):  # the same approval id, asked for in another org
            try:
                await stack.decider().decide(
                    approval["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id=MANAGER
                )
            except LookupError:
                return
            raise AssertionError("another org decided this org's approval")

    with_stack(scratch, body)
    [stored] = scratch.find("approvals", {})
    assert stored["status"] == "pending"
