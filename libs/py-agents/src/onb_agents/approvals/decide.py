"""The decision write: first decision wins (PRD FR-H2, FR-H3).

The web route and a Telegram press both end here. The decision is stored and `approval.decided` is
published in **one** outbox transaction (EVT-1), under a fixed idempotency key per approval, so:

* two decisions at once record exactly one: the bus drops the second key, and if both got past it the
  decision write applies to a `pending` approval only (`AlreadyDecidedError` rolls the loser back);
* a decision that is stored always has its event (a crash cannot lose the resume);
* only the approver decides: anyone else gets `NotApproverError`, which the API answers like a missing
  approval so it does not reveal whose approval it is.

Who decided and through which channel is stored (FR-H3); a Telegram decision is attributed to the
user the buttons were sent to (the approver), never to anything in the press.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from onb_contracts.approvals import APPROVAL_DECIDED, MAX_COMMENT_CHARS, ApprovalStatus, DecisionChannel
from onb_contracts.notify import Decision
from onb_data import Actor, Auditor, AuditOutcome
from onb_eventbus import EventBus

from .store import ApprovalStore

__all__ = [
    "DECIDE_ACTION",
    "AlreadyDecidedError",
    "ApprovalDecider",
    "ApprovalNotFoundError",
    "NotApproverError",
    "decision_key",
]

DECIDE_ACTION = "approval.decide"


class ApprovalNotFoundError(LookupError):
    """No such approval in this org."""


class NotApproverError(PermissionError):
    """The user is not this approval's approver."""


class AlreadyDecidedError(Exception):
    """Another decision (or a cancel) was recorded first."""


def decision_key(approval_id: str) -> str:
    """The idempotency key of an approval's one decision event."""
    return f"approval:{approval_id}:decided"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _may_decide(approval: Mapping[str, Any], user_id: str, role: str | None) -> bool:
    if approval.get("approver_id") is not None:
        return bool(approval["approver_id"] == user_id)
    return role is not None and approval.get("approver_role") == role


class ApprovalDecider:
    def __init__(
        self,
        approvals: ApprovalStore,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] = _utc_now,
        auditor: Auditor | None = None,
    ) -> None:
        self._approvals = approvals
        self._bus = bus
        self._clock = clock
        self._auditor = auditor

    async def decide(
        self,
        approval_id: str,
        *,
        decision: Decision,
        comment: str | None,
        channel: DecisionChannel,
        user_id: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Record the decision and publish `approval.decided`, atomically. Returns the approval as stored."""
        if comment is not None and len(comment) > MAX_COMMENT_CHARS:
            raise ValueError(f"the comment is at most {MAX_COMMENT_CHARS} characters")
        approval = await self._approvals.get(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(approval_id)
        if not _may_decide(approval, user_id, role):
            raise NotApproverError(approval_id)
        if approval["status"] != "pending":
            raise AlreadyDecidedError(approval_id)
        stored = {
            "decision": decision.value,
            "comment": comment,
            "channel": channel,
            "decided_by": user_id,
            "decided_at": self._clock(),
        }
        status: ApprovalStatus = "approved" if decision is Decision.APPROVE else "rejected"

        async def write() -> None:
            if not await self._approvals.decide_if_pending(approval_id, stored, status):
                raise AlreadyDecidedError(approval_id)
            if self._auditor is not None:  # same transaction: a decision whose audit write fails is not made
                await self._auditor.audit(
                    actor=Actor.user(user_id),
                    action=DECIDE_ACTION,
                    target=f"approval:{approval_id}",
                    outcome=AuditOutcome.SUCCESS,
                    reason=f"{decision.value}:{channel}",
                )

        payload = {
            "approval_id": approval_id,
            "run_id": approval["run_id"],
            "task_id": approval["task_id"],
            "decision": decision.value,
            "channel": channel,
        }
        result = await self._bus.publish(
            APPROVAL_DECIDED,
            payload,
            idempotency_key=decision_key(approval_id),
            run_id=approval["run_id"],
            change=write,
        )
        if result.duplicate:
            raise AlreadyDecidedError(approval_id)
        return {**approval, "status": status, "decision": stored}
