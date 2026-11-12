"""Approvals: the store, the approver rule and the first-decision-wins write (HIL-1, FR-H1-H3).

Over the real `DataStore` with the in-memory driver; the real bus and its transactions are proved in
`apps/orchestrator/tests_int`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_agents.approvals import (
    AlreadyDecidedError,
    ApprovalDecider,
    ApprovalNotFoundError,
    ApprovalSpec,
    ApprovalStore,
    NotApproverError,
    resolve_approver,
)
from onb_agents.runs.testing import OutboxStandIn
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.approvals import APPROVAL_DECIDED, DecisionChannel
from onb_contracts.notify import Decision
from onb_data import AuditOutcome, AuditQuery, tenant_scope
from onb_data.testing import InMemoryAuditLog

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 12, 10, 1, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _spec(**changes: Any) -> ApprovalSpec:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "task_id": "manager_approval",
        "hire_id": "h_1",
        "kind": "task",
        "rule": None,
        "approver_id": "u_mgr",
        "approver_role": None,
        "title": "Manager approves equipment",
        "items": ["Laptop", "Bitbucket access"],
        "sla_hours": 48,
    }
    return ApprovalSpec(**{**base, **changes})


def _world() -> tuple[ApprovalStore, ApprovalDecider, OutboxStandIn, MemoryDatabase]:
    db = MemoryDatabase()
    store = memory_store(db)
    bus = OutboxStandIn()
    approvals = ApprovalStore(store)
    return approvals, ApprovalDecider(approvals, bus, clock=lambda: LATER), bus, db


# --- the store ------------------------------------------------------------------------------------------


def test_an_approval_is_created_pending_with_a_deterministic_id() -> None:
    approvals, _, _, _ = _world()

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        with tenant_scope("org_a"):
            first = await approvals.create(_spec(), now=NOW)
            again = await approvals.create(_spec(), now=LATER)
            return first, again

    first, again = run(go())
    assert first["status"] == "pending"
    assert first["_id"].startswith("appr_")
    assert again["_id"] == first["_id"]
    assert again["requested_at"] == NOW  # the second create is a replay: it does not overwrite


def test_a_task_waiting_for_two_reasons_has_two_approvals() -> None:
    approvals, _, _, _ = _world()

    async def go() -> set[str]:
        with tenant_scope("org_a"):
            a = await approvals.create(_spec(kind="tool_call", rule="create_bitbucket_account"), now=NOW)
            b = await approvals.create(_spec(kind="tool_call", rule="update_one:documents_meta"), now=NOW)
            c = await approvals.create(_spec(kind="needs_human"), now=NOW)
            return {a["_id"], b["_id"], c["_id"]}

    assert len(run(go())) == 3


def test_a_tool_call_approval_needs_its_rule_and_the_others_have_none() -> None:
    approvals, _, _, _ = _world()

    async def go() -> None:
        with tenant_scope("org_a"):
            await approvals.create(_spec(kind="tool_call", rule=None), now=NOW)

    with pytest.raises(ValueError, match="rule"):
        run(go())

    async def other() -> None:
        with tenant_scope("org_a"):
            await approvals.create(_spec(kind="task", rule="x"), now=NOW)

    with pytest.raises(ValueError, match="rule"):
        run(other())


def test_the_packet_is_stored_once_on_the_approval() -> None:
    approvals, _, _, _ = _world()
    packet = {"summary": "Two standard items", "recommendation": "approve", "risk_flags": [], "cited_policies": []}

    async def go() -> dict[str, Any] | None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await approvals.set_packet(created["_id"], packet)
            await approvals.set_packet(created["_id"], {**packet, "recommendation": "reject"})
            return await approvals.get(created["_id"])

    found = run(go())
    assert found is not None
    assert found["packet"] == packet  # the first packet stays
    assert found["packet_failure"] is None


def test_a_packet_failure_is_recorded_as_a_code() -> None:
    approvals, _, _, _ = _world()

    async def go() -> dict[str, Any] | None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await approvals.set_packet(created["_id"], None, failure="budget_exceeded")
            return await approvals.get(created["_id"])

    found = run(go())
    assert found is not None
    assert found["packet"] is None
    assert found["packet_failure"] == "budget_exceeded"


def test_another_orgs_approval_does_not_exist() -> None:
    approvals, _, _, _ = _world()

    async def go() -> dict[str, Any] | None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
        with tenant_scope("org_b"):
            return await approvals.get(created["_id"])

    assert run(go()) is None


def test_approved_rules_lists_only_approved_tool_call_rules_of_that_task() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> tuple[str, ...]:
        with tenant_scope("org_a"):
            a = await approvals.create(_spec(kind="tool_call", rule="create_bitbucket_account"), now=NOW)
            await approvals.create(_spec(kind="tool_call", rule="update_one:documents_meta"), now=NOW)  # pending
            other = await approvals.create(
                _spec(task_id="other", kind="tool_call", rule="create_bitbucket_account"), now=NOW
            )
            await decider.decide(a["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr")
            await decider.decide(other["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr")
            return await approvals.approved_rules("run_1", "manager_approval")

    assert run(go()) == ("create_bitbucket_account",)


# --- the approver rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("approver", "hire", "expected"),
    [
        ({"rule": "variable", "ref": "manager_id"}, {"manager_id": "u_boss"}, ("u_boss", None)),
        ({"rule": "user", "ref": "u_fixed"}, {"manager_id": "u_boss"}, ("u_fixed", None)),
        ({"rule": "role", "ref": "HR_ADMIN"}, {"manager_id": "u_boss"}, (None, "HR_ADMIN")),
        (None, {"manager_id": "u_boss"}, ("u_boss", None)),  # a needs_human task without a rule: the manager
        (None, {}, (None, "HR")),  # no manager either: HR
        ({"rule": "variable", "ref": "buddy_id"}, {"manager_id": "u_boss"}, ("u_boss", None)),  # not a hire field
    ],
)
def test_the_approver_comes_from_the_rule_then_the_hires_manager_then_hr(
    approver: dict[str, str] | None, hire: dict[str, Any], expected: tuple[str | None, str | None]
) -> None:
    assert resolve_approver(approver, hire) == expected


# --- the first decision wins ----------------------------------------------------------------------------


def test_the_decision_is_stored_with_comment_channel_and_user_and_emitted() -> None:
    approvals, decider, bus, _ = _world()

    async def go() -> dict[str, Any]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            return await decider.decide(
                created["_id"], decision=Decision.REJECT, comment="Not this quarter", channel="web", user_id="u_mgr"
            )

    decided = run(go())
    assert decided["status"] == "rejected"
    assert decided["decision"] == {
        "decision": "reject",
        "comment": "Not this quarter",
        "channel": "web",
        "decided_by": "u_mgr",
        "decided_at": LATER,
    }
    [event] = bus.published
    assert event["type"] == APPROVAL_DECIDED
    assert event["payload"] == {
        "approval_id": decided["_id"],
        "run_id": "run_1",
        "task_id": "manager_approval",
        "decision": "reject",
        "channel": "web",
    }
    assert event["run_id"] == "run_1"


def test_a_decision_is_audited_with_who_decided_and_the_reason_is_its_channel() -> None:
    db = MemoryDatabase()
    approvals = ApprovalStore(memory_store(db))
    audit = InMemoryAuditLog()
    decider = ApprovalDecider(approvals, OutboxStandIn(), clock=lambda: LATER, auditor=audit)

    async def go() -> list[Any]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await decider.decide(
                created["_id"], decision=Decision.REJECT, comment="no", channel="telegram", user_id="u_mgr"
            )
            with pytest.raises(AlreadyDecidedError):  # a refused decision is not an action: nothing audited
                await decider.decide(
                    created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr"
                )
            return list((await audit.list(AuditQuery(), page=1, limit=10)).entries)

    [record] = run(go())
    assert (record.action, record.actor.id, record.outcome, record.reason) == (
        "approval.decide",
        "u_mgr",
        AuditOutcome.SUCCESS,
        "reject:telegram",
    )
    assert record.target.startswith("approval:appr_")


def test_the_second_decision_is_refused_and_changes_nothing() -> None:
    approvals, decider, bus, _ = _world()

    async def go() -> dict[str, Any] | None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await decider.decide(
                created["_id"], decision=Decision.APPROVE, comment="ok", channel="telegram", user_id="u_mgr"
            )
            with pytest.raises(AlreadyDecidedError):
                await decider.decide(
                    created["_id"], decision=Decision.REJECT, comment=None, channel="web", user_id="u_mgr"
                )
            return await approvals.get(created["_id"])

    stored = run(go())
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["decision"]["channel"] == "telegram"
    assert len(bus.published) == 1  # the loser published nothing


def test_concurrent_decisions_record_exactly_one() -> None:
    approvals, decider, bus, _ = _world()

    cases: list[tuple[Decision, DecisionChannel]] = [
        (Decision.APPROVE, "web"),
        (Decision.REJECT, "telegram"),
        (Decision.APPROVE, "telegram"),
        (Decision.REJECT, "web"),
    ]

    async def go() -> list[object]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            results = await asyncio.gather(
                *(
                    decider.decide(created["_id"], decision=d, comment=None, channel=c, user_id="u_mgr")
                    for d, c in cases
                ),
                return_exceptions=True,
            )
            return list(results)

    results = run(go())
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, AlreadyDecidedError)]
    assert len(winners) == 1
    assert len(losers) == 3
    assert len(bus.published) == 1


def test_only_the_approver_may_decide_and_anyone_else_gets_not_found() -> None:
    approvals, decider, bus, _ = _world()

    async def go() -> None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            with pytest.raises(NotApproverError):
                await decider.decide(
                    created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_other"
                )
            with pytest.raises(NotApproverError):  # a role does not stand in for a named approver
                await decider.decide(
                    created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_hr", role="HR"
                )
            stored = await approvals.get(created["_id"])
            assert stored is not None
            assert stored["status"] == "pending"

    run(go())
    assert bus.published == []


def test_a_role_approver_is_any_user_with_that_role() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> dict[str, Any]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(approver_id=None, approver_role="HR"), now=NOW)
            with pytest.raises(NotApproverError):
                await decider.decide(
                    created["_id"],
                    decision=Decision.APPROVE,
                    comment=None,
                    channel="web",
                    user_id="u_x",
                    role="MANAGER",
                )
            return await decider.decide(
                created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_hr", role="HR"
            )

    assert run(go())["decision"]["decided_by"] == "u_hr"


def test_deciding_a_missing_or_foreign_approval_is_not_found() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
        with tenant_scope("org_b"), pytest.raises(ApprovalNotFoundError):
            await decider.decide(
                created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr"
            )
        with tenant_scope("org_a"), pytest.raises(ApprovalNotFoundError):
            await decider.decide("appr_nobody", decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr")

    run(go())


def test_a_cancelled_approval_cannot_be_decided() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> list[str]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            cancelled = await approvals.cancel_pending("run_1", at=LATER)
            with pytest.raises(AlreadyDecidedError):
                await decider.decide(
                    created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr"
                )
            return cancelled

    assert len(run(go())) == 1


def test_a_comment_is_kept_but_never_longer_than_the_contract_allows() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await decider.decide(
                created["_id"], decision=Decision.APPROVE, comment="x" * 1001, channel="web", user_id="u_mgr"
            )

    with pytest.raises(ValueError, match="comment"):
        run(go())


def test_list_for_shows_a_user_their_approvals_newest_first_and_filters_by_status() -> None:
    approvals, decider, _, _ = _world()

    async def go() -> tuple[list[str], list[str], list[str]]:
        with tenant_scope("org_a"):
            a = await approvals.create(_spec(task_id="t1"), now=NOW)
            b = await approvals.create(_spec(task_id="t2"), now=LATER)
            await approvals.create(_spec(task_id="t3", approver_id="u_other"), now=LATER)
            await decider.decide(a["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr")
            everything = await approvals.list_for(user_id="u_mgr", role="MANAGER", status=None, skip=0, limit=10)
            pending = await approvals.list_for(user_id="u_mgr", role="MANAGER", status="pending", skip=0, limit=10)
            total = await approvals.count_for(user_id="u_mgr", role="MANAGER", status=None)
            assert total == 2
            return [x["task_id"] for x in everything], [x["_id"] for x in pending], [b["_id"]]

    everything, pending, expected = run(go())
    assert everything == ["t2", "t1"]
    assert pending == expected


def test_the_decision_write_applies_to_a_pending_approval_only() -> None:
    """M1: the store's own guard, not only the decider's checks before it."""
    approvals, _, _, _ = _world()

    async def go() -> tuple[bool, bool, dict[str, Any] | None]:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            first = await approvals.decide_if_pending(created["_id"], {"decision": "approve"}, "approved")
            second = await approvals.decide_if_pending(created["_id"], {"decision": "reject"}, "rejected")
            return first, second, await approvals.get(created["_id"])

    first, second, stored = run(go())
    assert (first, second) == (True, False)
    assert stored is not None
    assert (stored["status"], stored["decision"]) == ("approved", {"decision": "approve"})


class _KeylessBus(OutboxStandIn):
    """A bus that does not drop a repeated idempotency key, so only the decision write itself can refuse."""

    async def publish(self, event_type: str, payload: Any, *, idempotency_key: str, **kwargs: Any) -> Any:
        return await super().publish(
            event_type, payload, idempotency_key=f"{idempotency_key}:{len(self.published)}", **kwargs
        )


def test_a_decision_that_loses_the_write_publishes_nothing_even_without_event_dedup() -> None:
    """M2: the decision write and the event key are two guards; each must hold alone. The decider reads the
    approval as still pending (a racing reader), but the write has already been lost."""
    approvals = ApprovalStore(memory_store(MemoryDatabase()))
    bus = _KeylessBus()
    decider = ApprovalDecider(approvals, bus, clock=lambda: LATER)

    async def go() -> None:
        with tenant_scope("org_a"):
            created = await approvals.create(_spec(), now=NOW)
            await decider.decide(
                created["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_mgr"
            )
            real_get = approvals.get

            async def stale_get(approval_id: str) -> dict[str, Any] | None:
                found = await real_get(approval_id)
                return {**found, "status": "pending"} if found else None

            approvals.get = stale_get  # type: ignore[method-assign]
            with pytest.raises(AlreadyDecidedError):
                await decider.decide(
                    created["_id"], decision=Decision.REJECT, comment=None, channel="telegram", user_id="u_mgr"
                )

    run(go())
    assert len([e for e in bus.published if e["type"] == APPROVAL_DECIDED]) == 1
