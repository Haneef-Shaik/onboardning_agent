"""A Telegram press decides an approval, and only the first decision counts (HIL-1 box 2; FR-H2, FR-H3).

`decision.received` (NOTIF-1) carries ids and codes. `orchestrator.record_decision` attributes the press
to the user the buttons were sent to (the outbox message's `audience_user_id`) and records it through the
same write as the web route, so a web decision and a Telegram press at the same moment record exactly one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.approvals import AlreadyDecidedError, ApprovalDecider, ApprovalSpec, ApprovalStore
from onb_agents.runs.testing import OutboxStandIn
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.approvals import APPROVAL_DECIDED
from onb_contracts.notify import DECISION_RECEIVED, Decision
from onb_data import AuditLog, AuditQuery, DataStore, tenant_scope
from onb_eventbus import JobContext
from onb_orchestrator.consumer import record_decision_handler

ORG = "org_a"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class World:
    def __init__(self) -> None:
        self.store: DataStore = memory_store(MemoryDatabase())
        self.bus = OutboxStandIn()
        self.approvals = ApprovalStore(self.store)
        self.decider = ApprovalDecider(self.approvals, self.bus, clock=lambda: NOW)
        self.handler = record_decision_handler(self.store, self.bus, clock=lambda: NOW)

    async def seed(
        self, *, approver: str = "u_mgr", audience: str | None = "u_mgr", ref_id: str | None = None
    ) -> tuple[str, str]:
        """An approval and the outbox message its buttons were sent in; returns `(approval id, outbox id)`."""
        with tenant_scope(ORG):
            await self.store.tenant("users").insert_one({"_id": "u_mgr", "role": "MANAGER"})
            await self.store.tenant("users").insert_one({"_id": "u_other", "role": "MANAGER"})
            approval = await self.approvals.create(
                ApprovalSpec("run_1", "appr", "h_1", "task", None, approver, None, "Approve", ["Laptop"], 48), now=NOW
            )
            await self.store.tenant("mock_outbox").insert_one(
                {
                    "_id": "out_1",
                    "audience_user_id": audience,
                    "ref": {"type": "approval", "id": ref_id or approval["_id"]},
                    "buttons": [],
                }
            )
        return str(approval["_id"]), "out_1"

    async def press(
        self, approval_id: str, outbox_id: str, decision: str = "approve", ref_type: str = "approval"
    ) -> None:
        payload = {
            "ref_type": ref_type,
            "ref_id": approval_id,
            "decision": decision,
            "channel": "telegram",
            "outbox_id": outbox_id,
            "callback_query_id": "cq_1",
        }
        job = JobContext(
            "j", ORG, "orchestrator.record_decision", "ev", DECISION_RECEIVED, MappingProxyType(payload), None, 1
        )
        with tenant_scope(ORG):
            await self.handler(job)

    async def stored(self, approval_id: str) -> dict[str, Any]:
        with tenant_scope(ORG):
            found = await self.approvals.get(approval_id)
        assert found is not None
        return found


def test_a_telegram_approve_is_recorded_for_the_approver_on_the_telegram_channel() -> None:
    world = World()

    async def go() -> dict[str, Any]:
        approval_id, outbox_id = await world.seed()
        await world.press(approval_id, outbox_id, "approve")
        return await world.stored(approval_id)

    stored = run(go())
    assert stored["status"] == "approved"
    assert stored["decision"]["channel"] == "telegram"
    assert stored["decision"]["decided_by"] == "u_mgr"
    [event] = world.bus.published
    assert event["type"] == APPROVAL_DECIDED
    assert event["payload"]["channel"] == "telegram"


def test_a_telegram_decision_is_audited_to_the_approver() -> None:
    world = World()

    async def go() -> list[Any]:
        approval_id, outbox_id = await world.seed()
        await world.press(approval_id, outbox_id, "approve")
        with tenant_scope(ORG):
            return list((await AuditLog(world.store).list(AuditQuery(), page=1, limit=10)).entries)

    [record] = run(go())
    assert (record.action, record.actor.id, record.reason) == ("approval.decide", "u_mgr", "approve:telegram")


def test_a_telegram_reject_is_recorded() -> None:
    world = World()

    async def go() -> str:
        approval_id, outbox_id = await world.seed()
        await world.press(approval_id, outbox_id, "reject")
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "rejected"


def test_a_press_after_a_web_decision_changes_nothing() -> None:
    world = World()

    async def go() -> dict[str, Any]:
        approval_id, outbox_id = await world.seed()
        with tenant_scope(ORG):
            await world.decider.decide(
                approval_id, decision=Decision.APPROVE, comment="ok", channel="web", user_id="u_mgr"
            )
        await world.press(approval_id, outbox_id, "reject")
        return await world.stored(approval_id)

    stored = run(go())
    assert stored["status"] == "approved"
    assert stored["decision"]["channel"] == "web"
    assert len(world.bus.published) == 1


def test_the_web_decision_after_a_press_gets_already_decided() -> None:
    world = World()

    async def go() -> None:
        approval_id, outbox_id = await world.seed()
        await world.press(approval_id, outbox_id, "approve")
        with tenant_scope(ORG), pytest.raises(AlreadyDecidedError):
            await world.decider.decide(
                approval_id, decision=Decision.REJECT, comment=None, channel="web", user_id="u_mgr"
            )

    run(go())


def test_a_web_decision_and_a_telegram_press_at_once_record_exactly_one() -> None:
    """Box 2: whichever lands first is the decision; the other is refused (409 on the web, a no-op on Telegram)."""
    world = World()

    async def go() -> tuple[list[object], dict[str, Any]]:
        approval_id, outbox_id = await world.seed()

        async def web() -> object:
            with tenant_scope(ORG):
                return await world.decider.decide(
                    approval_id, decision=Decision.REJECT, comment=None, channel="web", user_id="u_mgr"
                )

        results = await asyncio.gather(web(), world.press(approval_id, outbox_id, "approve"), return_exceptions=True)
        return list(results), await world.stored(approval_id)

    results, stored = run(go())
    assert len(world.bus.published) == 1  # one event, so one resume
    web_result, press_result = results
    assert press_result is None  # a press never raises: the webhook always acknowledges
    if stored["decision"]["channel"] == "web":
        assert isinstance(web_result, dict)
        assert stored["status"] == "rejected"
    else:
        assert isinstance(web_result, AlreadyDecidedError)  # the API answers this 409
        assert stored["status"] == "approved"


def test_a_press_by_a_message_addressed_to_someone_else_decides_nothing() -> None:
    world = World()

    async def go() -> str:
        approval_id, outbox_id = await world.seed(audience="u_other")  # the buttons went to another user
        await world.press(approval_id, outbox_id)
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "pending"


def test_a_message_that_is_not_this_approvals_decides_nothing() -> None:
    world = World()

    async def go() -> str:
        approval_id, outbox_id = await world.seed(ref_id="appr_something_else")
        await world.press(approval_id, outbox_id)
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "pending"


@pytest.mark.parametrize(
    "payload",
    [
        {"ref_type": "change_request", "ref_id": "x", "decision": "approve", "outbox_id": "out_1"},
        {"ref_type": "approval", "ref_id": "appr_x", "decision": "maybe", "outbox_id": "out_1"},
        {"ref_type": "approval", "ref_id": "appr_x", "decision": "approve"},
        {"ref_type": "approval", "ref_id": 7, "decision": "approve", "outbox_id": "out_1"},
        {"ref_type": "approval", "ref_id": "appr_missing", "decision": "approve", "outbox_id": "out_missing"},
    ],
)
def test_a_press_that_is_not_a_decision_on_an_approval_is_ignored(payload: dict[str, Any]) -> None:
    world = World()

    async def go() -> str:
        approval_id, _ = await world.seed()
        job = JobContext(
            "j", ORG, "orchestrator.record_decision", "ev", DECISION_RECEIVED, MappingProxyType(payload), None, 1
        )
        with tenant_scope(ORG):
            await world.handler(job)
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "pending"
    assert world.bus.published == []


def test_another_orgs_press_cannot_decide_this_orgs_approval() -> None:
    world = World()

    async def go() -> str:
        approval_id, outbox_id = await world.seed()
        payload = {"ref_type": "approval", "ref_id": approval_id, "decision": "approve", "outbox_id": outbox_id}
        job = JobContext(
            "j", "org_b", "orchestrator.record_decision", "ev", DECISION_RECEIVED, MappingProxyType(payload), None, 1
        )
        with tenant_scope("org_b"):
            await world.handler(job)
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "pending"


def test_a_press_of_another_kind_is_ignored_even_when_its_ids_name_a_real_approval_and_message() -> None:
    world = World()

    async def go() -> str:
        approval_id, outbox_id = await world.seed()
        await world.press(approval_id, outbox_id, "approve", ref_type="change_request")
        return str((await world.stored(approval_id))["status"])

    assert run(go()) == "pending"


def test_a_decision_is_attributed_to_the_messages_audience_whatever_the_event_claims() -> None:
    world = World()

    async def go() -> dict[str, Any]:
        approval_id, outbox_id = await world.seed()
        payload = {
            "ref_type": "approval",
            "ref_id": approval_id,
            "decision": "approve",
            "outbox_id": outbox_id,
            "user_id": "u_other",  # a claim the event makes about who pressed: never believed
        }
        job = JobContext(
            "j", ORG, "orchestrator.record_decision", "ev", DECISION_RECEIVED, MappingProxyType(payload), None, 1
        )
        with tenant_scope(ORG):
            await world.handler(job)
        return await world.stored(approval_id)

    assert run(go())["decision"]["decided_by"] == "u_mgr"
