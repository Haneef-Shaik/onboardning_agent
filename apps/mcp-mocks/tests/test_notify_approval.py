"""The `notify.approval` consumer: the approver is told in-app and on the Telegram mock, with Approve/Reject
buttons addressed to them (HIL-1; PRD FR-H2, U5; ADR 0013 consequence for HIL-1)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.notify import APPROVAL_REQUESTED, Decision
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_mcp_mocks.notify.approvals import approval_notify_handler, ensure_approval_notify_indexes
from onb_mcp_mocks.notify.in_app import MalformedEventError, ensure_in_app_indexes
from onb_mcp_mocks.notify.outbox import TelegramMockChannel
from onb_mcp_mocks.telegram import TelegramMockTransport

ORG_A, ORG_B = "org_acme", "org_globex"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
PACKET = {
    "summary": "A MacBook Pro 14 and Bitbucket access, both standard for an engineer.",
    "recommendation": "approve",
    "risk_flags": ["new_laptop"],
    "cited_policies": ["kb_access_policy"],
}


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
        "request": {"items": ["MacBook Pro 14", "Bitbucket access"]},
        "packet": PACKET,
        "packet_failure": None,
        "requested_at": NOW,
        "sla_hours": 48,
        "decision": None,
        **changes,
    }


def world(*approvals: dict[str, Any]) -> MemoryDatabase:
    db = MemoryDatabase()
    store = memory_store(db)

    async def seed() -> None:
        await ensure_approval_notify_indexes(store)
        await ensure_in_app_indexes(store)  # the unique dedupe key the production service creates
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


def deliver(db: MemoryDatabase, approval_id: str = "appr_1", *, org: str = ORG_A, event_id: str = "e1") -> None:
    payload: Mapping[str, Any] = {"approval_id": approval_id, "run_id": "run_1", "task_id": "manager_approval"}
    job = JobContext(f"j-{event_id}", org, "notify.approval", event_id, APPROVAL_REQUESTED, payload, "run_1", 1)
    store = memory_store(db)
    handler = approval_notify_handler(store, TelegramMockChannel(store, TelegramMockTransport()), clock=lambda: NOW)

    async def go() -> None:
        with tenant_scope(org):
            await handler(job)

    asyncio.run(go())


def test_the_approver_gets_an_in_app_notification_that_names_the_approval() -> None:
    db = world(approval())
    deliver(db)
    [note] = db.raw("notifications")
    assert (note["org_id"], note["user_id"], note["kind"]) == (ORG_A, "u_acme_mgr_ravi", "approval.requested")
    assert note["ref"] == {"type": "approval", "id": "appr_1"}
    assert note["run_id"] == "run_1"
    assert "Manager approves equipment & access" in note["body"]


def test_the_approver_gets_a_telegram_message_with_approve_and_reject_addressed_to_them() -> None:
    db = world(approval())
    deliver(db)
    [message] = db.raw("mock_outbox")
    assert message["audience_user_id"] == "u_acme_mgr_ravi"  # a manager's outbox shows only these
    assert message["ref"] == {"type": "approval", "id": "appr_1"}
    assert (message["run_id"], message["task_id"]) == ("run_1", "manager_approval")
    assert [(b["text"], b["decision"]) for b in message["buttons"]] == [
        ("Approve", Decision.APPROVE.value),
        ("Reject", Decision.REJECT.value),
    ]
    registered = {c["_id"]: c["org_id"] for c in db.raw("telegram_callbacks")}
    assert registered == {b["callback_data"]: ORG_A for b in message["buttons"]}  # a press finds its org
    assert message["recipient"]["token"] is None  # no vault token, no raw value: the mock keeps a masked label
    assert message["recipient"]["masked"] == "user:u_acme_mgr_ravi"


def test_the_message_carries_the_masked_packet_so_one_press_is_enough() -> None:
    db = world(approval())
    deliver(db)
    [message] = db.raw("mock_outbox")
    text = message["text"]
    for part in ("Manager approves equipment & access", "MacBook Pro 14", "Bitbucket access", "approve", "new_laptop"):
        assert part in text
    assert "A MacBook Pro 14 and Bitbucket access, both standard" in text


def test_a_tool_call_approval_says_which_rule_it_grants_and_for_how_long() -> None:
    db = world(approval(kind="tool_call", rule="create_bitbucket_account", packet=None))
    deliver(db)
    [message] = db.raw("mock_outbox")
    [note] = db.raw("notifications")
    for text in (message["text"], note["body"]):
        assert "create_bitbucket_account" in text
    assert "whole task" in message["text"]  # approving lets the agent use the rule for the rest of the task


def test_a_needs_human_approval_says_the_agent_asked_for_a_person() -> None:
    db = world(approval(kind="needs_human", packet=None))
    deliver(db)
    [message] = db.raw("mock_outbox")
    assert "asked for a human" in message["text"]


def test_an_approval_without_a_packet_still_asks_and_says_so() -> None:
    db = world(approval(packet=None, packet_failure="invalid_output"))
    deliver(db)
    [message] = db.raw("mock_outbox")
    assert "No AI summary" in message["text"]
    assert len(message["buttons"]) == 2


def test_free_text_in_a_packet_is_redacted_before_it_leaves() -> None:
    leaked = {**PACKET, "summary": "Call the hire on 9876543210 or mail a.b@example.com"}
    db = world(approval(packet=leaked))
    deliver(db)
    [message] = db.raw("mock_outbox")
    [note] = db.raw("notifications")
    blob = message["text"] + note["body"]
    assert "9876543210" not in blob
    assert "a.b@example.com" not in blob


def test_the_same_event_delivered_again_tells_the_approver_once() -> None:
    db = world(approval())
    deliver(db)
    deliver(db)
    assert len(db.raw("mock_outbox")) == 1
    assert len(db.raw("notifications")) == 1
    assert len(db.raw("telegram_callbacks")) == 2


def test_a_role_approver_is_every_user_with_the_role() -> None:
    db = world(approval(approver_id=None, approver_role="HR"))
    deliver(db)
    assert sorted(n["user_id"] for n in db.raw("notifications")) == ["u_acme_hr1", "u_acme_hr2"]
    assert sorted(m["audience_user_id"] for m in db.raw("mock_outbox")) == ["u_acme_hr1", "u_acme_hr2"]


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled"])
def test_an_approval_that_is_no_longer_pending_sends_no_buttons(status: str) -> None:
    db = world(approval(status=status))
    deliver(db)
    assert db.raw("mock_outbox") == []
    assert db.raw("telegram_callbacks") == []


def test_another_orgs_approval_tells_nobody() -> None:
    db = world(approval())
    deliver(db, org=ORG_B)
    assert db.raw("mock_outbox") == []
    assert db.raw("notifications") == []


def test_a_missing_approval_tells_nobody() -> None:
    db = world(approval())
    deliver(db, "appr_missing")
    assert db.raw("mock_outbox") == []
    assert db.raw("notifications") == []


def test_a_malformed_payload_is_refused() -> None:
    db = world(approval())
    store = memory_store(db)
    handler = approval_notify_handler(store, TelegramMockChannel(store, TelegramMockTransport()))
    job = JobContext("j", ORG_A, "notify.approval", "e1", APPROVAL_REQUESTED, {"approval_id": 7}, None, 1)

    async def go() -> None:
        with tenant_scope(ORG_A):
            await handler(job)

    with pytest.raises(MalformedEventError):
        asyncio.run(go())


def test_an_approver_who_has_left_the_org_is_skipped_not_retried() -> None:
    db = world(approval(approver_id="u_gone"))
    deliver(db)  # no exception: a retry would not bring them back
    assert db.raw("mock_outbox") == []
    assert db.raw("notifications") == []
