"""HIL-2 against the compose Mongo replica set (PRD FR-H4): a real run pauses on its approval task, then the SLA
sweep (fake clock, no waiting) reminds the approver once and escalates to HR once, through the real outbox, and
mcp-notify's real `notify.approval_sla` worker delivers each message once. A restarted worker (a new connection,
a new job, nothing in memory) sends nothing again, and a stamp whose transaction fails is rolled back with its
event, so the next pass sends it exactly once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from onb_agents.approvals import ApprovalStore
from onb_agents.approvals.sla import SlaPolicy
from onb_agents.runs import RunStarter, RunStore, run_bus
from onb_agents.runs.testing import hire_variables_for
from onb_contracts.notify import APPROVAL_OVERDUE, APPROVAL_SLA_CONSUMER, NOTIFY_SUBSCRIPTIONS
from onb_data import AuditLog, DataStore, connect, tenant_scope
from onb_eventbus import MongoEventBus
from onb_mcp_mocks.notify.outbox import TelegramMockChannel
from onb_mcp_mocks.notify.sla import approval_sla_handler
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_orchestrator.approval_sla import ApprovalSlaJob, SlaReport
from onb_orchestrator.worker import prepare

from .conftest import Scratch
from .test_approvals_against_mongo import EXTRA_COLLECTIONS, MANAGER, ORG_A, approval_template

POLICY = SlaPolicy(default_sla_hours=24, remind_after_sla=1.0, escalate_after_sla=2.0, escalate_to_role="HR")
SLA_HOURS = 48  # the template's approval task
USERS = [
    {"_id": MANAGER, "role": "MANAGER"},
    {"_id": "u_acme_hr1", "role": "HR"},
    {"_id": "u_acme_hr2", "role": "HR"},
]


class Clock:
    def __init__(self) -> None:
        self.now: datetime | None = None

    def __call__(self) -> datetime:
        assert self.now is not None
        return self.now


def _collections(scratch: Scratch) -> None:
    existing = set(scratch.db.list_collection_names())
    for name in (*EXTRA_COLLECTIONS, "notifications", "orgs", "telegram_callbacks"):
        if name not in existing:
            scratch.db.create_collection(name)


async def _paused_approval(store: DataStore) -> dict[str, Any]:
    """A real run of the approval template, driven by the production worker until it waits on the manager."""
    worker = await prepare(store, stub_delay=0.0)
    await store.shared("orgs").insert_one({"_id": ORG_A, "name": "Acme"})
    with tenant_scope(ORG_A):
        await store.tenant("users").insert_many([dict(u) for u in USERS])
        await store.tenant("new_hires").update_one({"_id": "h_1"}, {"$set": {"manager_id": MANAGER}}, upsert=True)
        started = await RunStarter(RunStore(store), run_bus(store), AuditLog(store)).start(
            template=approval_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
        )
    while await worker.worker.run_once() is not None:
        pass
    with tenant_scope(ORG_A):
        [approval] = await ApprovalStore(store).for_task(str(started.run["_id"]), "manager_approval")
    assert approval["status"] == "pending"
    assert approval["sla_hours"] == SLA_HOURS
    return approval


async def _sweep(scratch: Scratch, clock: Clock, at: datetime) -> SlaReport:
    """One pass by a freshly started worker: its own connection, bus and job, nothing carried over."""
    store = connect(scratch.uri, scratch.name)
    try:
        clock.now = at
        return await ApprovalSlaJob(store, run_bus(store), POLICY, clock=clock).run_once()
    finally:
        await store.close()


async def _notify(scratch: Scratch) -> int:
    """mcp-notify's real worker for `notify.approval_sla`, run until the queue is empty; jobs processed."""
    store = connect(scratch.uri, scratch.name)
    try:
        bus = MongoEventBus(store, subscriptions=NOTIFY_SUBSCRIPTIONS)
        handler = approval_sla_handler(store, TelegramMockChannel(store, TelegramMockTransport()))
        worker = bus.worker({APPROVAL_SLA_CONSUMER: handler})
        processed = 0
        while await worker.run_once() is not None:
            processed += 1
        return processed
    finally:
        await store.close()


def _run(scratch: Scratch, body: Callable[[DataStore], Awaitable[Any]]) -> Any:
    _collections(scratch)

    async def go() -> Any:
        store = connect(scratch.uri, scratch.name)
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(go())


def _overdue_events(scratch: Scratch) -> list[tuple[str, str]]:
    return [(e["org_id"], e["payload"]["stage"]) for e in scratch.find("events", {"type": APPROVAL_OVERDUE})]


def _messages(scratch: Scratch) -> list[tuple[str, str]]:
    return sorted(
        (m["audience_user_id"], m["sla_stage"]) for m in scratch.find("mock_outbox", {"sla_stage": {"$exists": True}})
    )


def test_a_paused_approval_is_reminded_once_then_escalated_once_and_a_restart_repeats_nothing(scratch: Scratch) -> None:
    approval = _run(scratch, _paused_approval)
    requested: datetime = approval["requested_at"]
    clock = Clock()

    async def timeline() -> list[SlaReport]:
        reports = []
        for hours in (1, 47.9, 48, 48.1, 60, 95.9, 96, 97, 500):
            reports.append(await _sweep(scratch, clock, requested + timedelta(hours=hours)))
            if hours in (48, 96):
                await _notify(scratch)
        await _notify(scratch)  # a notify worker restarted afterwards finds nothing left
        return reports

    reports = asyncio.run(timeline())
    assert reports == [
        SlaReport(),
        SlaReport(),
        SlaReport(reminders=1),  # 48 h: overdue
        SlaReport(),
        SlaReport(),
        SlaReport(),
        SlaReport(escalations=1),  # 96 h: the second threshold
        SlaReport(),
        SlaReport(),
    ]
    assert _overdue_events(scratch) == [(ORG_A, "reminder"), (ORG_A, "escalation")]
    stored = scratch.find_one("approvals", {"_id": approval["_id"]})
    assert stored is not None
    assert stored["reminded_at"] == requested + timedelta(hours=48)
    assert stored["escalated_at"] == requested + timedelta(hours=96)
    assert stored["status"] == "pending"
    # one reminder to the manager (with the decision buttons), one escalation to each HR user (without)
    assert _messages(scratch) == [(MANAGER, "reminder"), ("u_acme_hr1", "escalation"), ("u_acme_hr2", "escalation")]
    kinds = sorted(
        (n["user_id"], n["kind"])
        for n in scratch.find("notifications", {"kind": {"$regex": "^approval\\.(reminder|escalated)$"}})
    )
    assert kinds == [
        (MANAGER, "approval.reminder"),
        ("u_acme_hr1", "approval.escalated"),
        ("u_acme_hr2", "approval.escalated"),
    ]
    jobs = scratch.find("jobs", {"consumer": APPROVAL_SLA_CONSUMER})
    assert [j["status"] for j in jobs] == ["done", "done"]


def test_a_stamp_whose_transaction_fails_is_rolled_back_with_its_event_and_sent_once_later(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = _run(scratch, _paused_approval)
    requested: datetime = approval["requested_at"]
    clock = Clock()
    original = ApprovalStore.mark_stage

    async def stamp_then_crash(self: ApprovalStore, *args: Any, **kwargs: Any) -> bool:
        await original(self, *args, **kwargs)
        raise RuntimeError("the worker died inside the transaction")

    monkeypatch.setattr(ApprovalStore, "mark_stage", stamp_then_crash)
    crashed = asyncio.run(_sweep(scratch, clock, requested + timedelta(hours=48)))
    assert crashed == SlaReport(failures=1)
    assert _overdue_events(scratch) == []  # no event...
    stored = scratch.find_one("approvals", {"_id": approval["_id"]})
    assert stored is not None
    assert stored.get("reminded_at") is None  # ...and no stamp: they commit together or not at all

    monkeypatch.setattr(ApprovalStore, "mark_stage", original)
    assert asyncio.run(_sweep(scratch, clock, requested + timedelta(hours=49))) == SlaReport(reminders=1)
    assert asyncio.run(_sweep(scratch, clock, requested + timedelta(hours=50))) == SlaReport()
    assert _overdue_events(scratch) == [(ORG_A, "reminder")]


def test_a_decision_before_the_threshold_means_no_reminder_ever(scratch: Scratch) -> None:
    approval = _run(scratch, _paused_approval)
    requested: datetime = approval["requested_at"]

    async def decide(store: DataStore) -> None:
        with tenant_scope(ORG_A):
            await ApprovalStore(store).decide_if_pending(approval["_id"], {"decision": "approve"}, "approved")

    _run(scratch, decide)
    assert asyncio.run(_sweep(scratch, Clock(), requested + timedelta(hours=500))) == SlaReport()
    assert _overdue_events(scratch) == []
