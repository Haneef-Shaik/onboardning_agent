"""The `notify.approval_sla` consumer (HIL-2, FR-H4): an overdue approval's reminder reaches its approver, and its
escalation reaches HR, in-app and on the Telegram mock; each once, however often the event is delivered."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.notify import APPROVAL_OVERDUE, APPROVAL_SLA_CONSUMER, Decision
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_mcp_mocks.notify.approvals import ensure_approval_notify_indexes
from onb_mcp_mocks.notify.in_app import MalformedEventError, ensure_in_app_indexes
from onb_mcp_mocks.notify.outbox import TelegramMockChannel
from onb_mcp_mocks.notify.sla import approval_sla_handler
from onb_mcp_mocks.telegram import TelegramMockTransport

ORG_A, ORG_B = "org_acme", "org_globex"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def approval(**changes: Any) -> dict[str, Any]:
    return {
        "_id": "appr_1",
        "run_id": "run_1",
        "task_id": "manager_approval",
        "hire_id": "h_acme_001",
        "kind": "task",
        "rule": None,
        "status": "pending",
        "approver_id": "u_acme_mgr_ravi",
        "approver_role": None,
        "title": "Manager approves equipment & access",
        "request": {"items": ["MacBook Pro 14"]},
        "packet": None,
        "packet_failure": None,
        "requested_at": NOW,
        "sla_hours": 48,
        "decision": None,
        # the sweep stamps a stage in the transaction that publishes its event (ADR 0027)
        "reminded_at": NOW,
        "escalated_at": NOW,
        **changes,
    }


def world(*approvals: dict[str, Any]) -> MemoryDatabase:
    db = MemoryDatabase()
    store = memory_store(db)

    async def seed() -> None:
        await ensure_approval_notify_indexes(store)
        await ensure_in_app_indexes(store)
        with tenant_scope(ORG_A):
            await store.tenant("users").insert_many(
                [
                    {"_id": "u_acme_mgr_ravi", "role": "MANAGER"},
                    {"_id": "u_acme_hr1", "role": "HR"},
                    {"_id": "u_acme_hr2", "role": "HR"},
                    {"_id": "u_acme_hradmin", "role": "HR_ADMIN"},
                ]
            )
            for doc in approvals:
                await store.tenant("approvals").insert_one(doc)
        with tenant_scope(ORG_B):
            await store.tenant("users").insert_one({"_id": "u_globex_hr1", "role": "HR"})

    asyncio.run(seed())
    return db


def payload(stage: str = "reminder", **changes: Any) -> dict[str, Any]:
    return {
        "approval_id": "appr_1",
        "run_id": "run_1",
        "task_id": "manager_approval",
        "stage": stage,
        "escalate_to": "HR" if stage == "escalation" else None,
        **changes,
    }


def deliver(db: MemoryDatabase, body: Mapping[str, Any], *, org: str = ORG_A, event_id: str = "e1") -> None:
    job = JobContext(f"j-{event_id}", org, APPROVAL_SLA_CONSUMER, event_id, APPROVAL_OVERDUE, body, "run_1", 1)
    store = memory_store(db)
    handler = approval_sla_handler(store, TelegramMockChannel(store, TelegramMockTransport()))

    async def go() -> None:
        with tenant_scope(org):
            await handler(job)

    asyncio.run(go())


# --- the reminder -----------------------------------------------------------------------------------------


def test_a_reminder_tells_the_approver_in_app_and_on_telegram_with_the_decision_buttons() -> None:
    db = world(approval())
    deliver(db, payload("reminder"))
    [note] = db.raw("notifications")
    assert (note["org_id"], note["user_id"], note["kind"]) == (ORG_A, "u_acme_mgr_ravi", "approval.reminder")
    assert note["ref"] == {"type": "approval", "id": "appr_1"}
    assert "overdue" in note["body"].lower()
    [message] = db.raw("mock_outbox")
    assert message["audience_user_id"] == "u_acme_mgr_ravi"
    assert message["ref"] == {"type": "approval", "id": "appr_1"}
    assert message["sla_stage"] == "reminder"
    assert message["text"].startswith("Reminder: this approval is overdue")
    assert [(b["text"], b["decision"]) for b in message["buttons"]] == [
        ("Approve", Decision.APPROVE.value),
        ("Reject", Decision.REJECT.value),
    ]
    assert {c["org_id"] for c in db.raw("telegram_callbacks")} == {ORG_A}


def test_a_role_approvals_reminder_goes_to_every_user_of_the_role() -> None:
    db = world(approval(approver_id=None, approver_role="HR"))
    deliver(db, payload("reminder"))
    assert sorted(m["audience_user_id"] for m in db.raw("mock_outbox")) == ["u_acme_hr1", "u_acme_hr2"]


def test_the_same_reminder_delivered_again_tells_the_approver_once() -> None:
    db = world(approval())
    deliver(db, payload("reminder"))
    deliver(db, payload("reminder"))  # a redelivered job: same event
    assert len(db.raw("notifications")) == 1
    assert len(db.raw("mock_outbox")) == 1


def test_a_reminder_after_a_partial_delivery_completes_it_without_repeating() -> None:
    # the in-app note was stored, then the worker died before the Telegram message: the redelivery sends only that
    db = world(approval())
    deliver(db, payload("reminder"))
    db.collections["mock_outbox"].documents.clear()
    deliver(db, payload("reminder"))
    assert len(db.raw("notifications")) == 1
    assert len(db.raw("mock_outbox")) == 1


# --- the escalation ---------------------------------------------------------------------------------------


def test_an_escalation_tells_every_hr_user_and_not_the_approver() -> None:
    db = world(approval())
    deliver(db, payload("escalation"))
    notes = db.raw("notifications")
    assert sorted(n["user_id"] for n in notes) == ["u_acme_hr1", "u_acme_hr2"]
    assert {n["kind"] for n in notes} == {"approval.escalated"}
    messages = db.raw("mock_outbox")
    assert sorted(m["audience_user_id"] for m in messages) == ["u_acme_hr1", "u_acme_hr2"]
    for message in messages:
        assert message["sla_stage"] == "escalation"
        assert message["text"].startswith("Escalated: an approval is overdue")
        assert "u_acme_mgr_ravi" in message["text"]  # who it waits on
        assert message["buttons"] == []  # HR is told; only the approver decides (HIL-1)
    assert db.raw("telegram_callbacks") == []


def test_the_escalation_goes_to_the_role_the_event_names() -> None:
    db = world(approval())
    deliver(db, payload("escalation", escalate_to="HR_ADMIN"))
    assert [m["audience_user_id"] for m in db.raw("mock_outbox")] == ["u_acme_hradmin"]


def test_the_same_escalation_delivered_again_tells_hr_once() -> None:
    db = world(approval())
    deliver(db, payload("escalation"))
    deliver(db, payload("escalation"))
    assert len(db.raw("notifications")) == 2
    assert len(db.raw("mock_outbox")) == 2


def test_a_reminder_and_an_escalation_are_separate_messages() -> None:
    db = world(approval(approver_id="u_acme_hr1"))  # an HR approver gets both, one of each
    deliver(db, payload("reminder"), event_id="e1")
    deliver(db, payload("escalation"), event_id="e2")
    mine = [m["sla_stage"] for m in db.raw("mock_outbox") if m["audience_user_id"] == "u_acme_hr1"]
    assert sorted(mine) == ["escalation", "reminder"]


# --- nothing to say, or a bad event -------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled"])
@pytest.mark.parametrize("stage", ["reminder", "escalation"])
def test_an_approval_decided_meanwhile_tells_nobody(status: str, stage: str) -> None:
    db = world(approval(status=status))
    deliver(db, payload(stage))
    assert db.raw("notifications") == []
    assert db.raw("mock_outbox") == []


def test_another_orgs_approval_tells_nobody() -> None:
    db = world(approval())
    deliver(db, payload("escalation"), org=ORG_B)
    assert db.raw("notifications") == []
    assert db.raw("mock_outbox") == []


def test_free_text_is_redacted_before_it_leaves() -> None:
    db = world(approval(title="Call 9876543210 about the laptop"))
    deliver(db, payload("reminder"), event_id="e1")
    deliver(db, payload("escalation"), event_id="e2")
    blob = "".join(m["text"] for m in db.raw("mock_outbox")) + "".join(n["body"] for n in db.raw("notifications"))
    assert "9876543210" not in blob


@pytest.mark.parametrize(("stage", "field"), [("reminder", "reminded_at"), ("escalation", "escalated_at")])
def test_an_event_for_a_stage_the_sweep_never_stamped_tells_nobody(stage: str, field: str) -> None:
    # review fix: the bus is a trust boundary; a forged or early `approval.overdue` changes nothing
    db = world(approval(**{field: None}))
    deliver(db, payload(stage))
    assert db.raw("notifications") == []
    assert db.raw("mock_outbox") == []


@pytest.mark.parametrize("stage", ["reminder", "escalation"])
def test_the_same_stage_published_again_under_another_event_tells_each_user_once(stage: str) -> None:
    db = world(approval())
    deliver(db, payload(stage), event_id="e1")
    deliver(db, payload(stage), event_id="e2")
    deliver(db, payload(stage), event_id="e3")
    users = [n["user_id"] for n in db.raw("notifications")]
    assert sorted(users) == sorted(set(users))
    assert len(db.raw("mock_outbox")) == len(users)


def test_a_reminder_does_not_stop_the_first_approval_message_from_reaching_the_approver() -> None:
    # review fix: HIL-1's redelivery check must not mistake a reminder for its own message
    from onb_contracts.notify import APPROVAL_REQUESTED
    from onb_mcp_mocks.notify.approvals import approval_notify_handler

    db = world(approval())
    deliver(db, payload("reminder"))
    store = memory_store(db)
    handler = approval_notify_handler(store, TelegramMockChannel(store, TelegramMockTransport()))
    body = {"approval_id": "appr_1", "run_id": "run_1", "task_id": "manager_approval"}
    job = JobContext("j-req", ORG_A, "notify.approval", "e-req", APPROVAL_REQUESTED, body, "run_1", 1)

    async def go() -> None:
        with tenant_scope(ORG_A):
            await handler(job)

    asyncio.run(go())
    stages = sorted(str(m.get("sla_stage")) for m in db.raw("mock_outbox"))
    assert stages == ["None", "reminder"]  # the original message is sent too


@pytest.mark.parametrize(
    "bad",
    [
        {"approval_id": "nope"},
        {"approval_id": None},
        {"stage": "nag"},
        {"stage": None},
        {"stage": "escalation", "escalate_to": "MANAGER"},
        {"stage": "escalation", "escalate_to": None},
    ],
)
def test_a_malformed_event_is_refused(bad: dict[str, Any]) -> None:
    db = world(approval())
    with pytest.raises(MalformedEventError):
        deliver(db, {**payload(), **bad})
    assert db.raw("mock_outbox") == []
