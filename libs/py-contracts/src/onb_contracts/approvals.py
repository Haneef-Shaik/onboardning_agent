"""Approval contracts: the approval record, the decision and its events (PRD FR-H1-H3, FR-H5, §10; HIL-1).

An approval is one human decision a run waits for. Three things open one (ADR 0020):

* `task`: an `approval` task of the template (FR-H1). The Approvals Agent prepares the `packet`;
* `tool_call`: an agent's tool call the hub refused with `approval_required` (FR-H5); `rule` names the
  manifest's `requires_approval` rule that an approve grants for the task's next attempt;
* `needs_human`: an agent answered `needs_human`; a human decides whether the task may go on.

`POST /approvals/{id}/decision` and a Telegram press both record the decision through one write
that applies to a `pending` approval only: the first decision wins, a later one is a 409 on the web and
a no-op from Telegram. Records hold ids, codes and masked text only; no raw PII.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from onb_contracts.agent_outputs import ApprovalPacket
from onb_contracts.envelope import Envelope
from onb_contracts.inputs import RequestModel
from onb_contracts.notify import (
    APPROVAL_DECIDED,
    APPROVAL_NOTIFY_CONSUMER,
    APPROVAL_REQUESTED,
    RECORD_DECISION_CONSUMER,
    RESUME_APPROVAL_CONSUMER,
    Decision,
)

__all__ = [
    "APPROVALS_COLLECTION",
    "APPROVAL_DECIDED",
    "APPROVAL_ID_PATTERN",
    "APPROVAL_NOTIFY_CONSUMER",
    "APPROVAL_REF_TYPE",
    "APPROVAL_REQUESTED",
    "MAX_COMMENT_CHARS",
    "RECORD_DECISION_CONSUMER",
    "RESUME_APPROVAL_CONSUMER",
    "ApprovalDecision",
    "ApprovalDecisionEnvelope",
    "ApprovalDecisionRequest",
    "ApprovalEnvelope",
    "ApprovalKind",
    "ApprovalListEnvelope",
    "ApprovalRecord",
    "ApprovalStatus",
    "DecisionChannel",
    "approval_id_for",
]

APPROVALS_COLLECTION: Final = "approvals"
APPROVAL_REF_TYPE: Final = "approval"
"""`ref.type` of an outbox message and `ref_type` of `decision.received` for an approval's buttons."""
APPROVAL_ID_PATTERN: Final = r"^appr_[A-Za-z0-9_]{1,64}$"
MAX_COMMENT_CHARS: Final = 1000

ApprovalKind = Literal["task", "tool_call", "needs_human"]
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled"]
DecisionChannel = Literal["web", "telegram"]

_FROZEN = ConfigDict(frozen=True, extra="forbid", json_schema_serialization_defaults_required=True)


def approval_id_for(digest: str) -> str:
    """`appr_` plus a hex digest of (run, task, kind, rule): one approval per reason a task waits."""
    return f"appr_{digest}"


class ApprovalDecisionRequest(RequestModel):
    """`POST /approvals/{id}/decision`: approve or reject, with an optional comment."""

    decision: Decision
    comment: Annotated[StrictStr, Field(max_length=MAX_COMMENT_CHARS)] | None = None


class ApprovalDecision(BaseModel):
    """What was decided, by whom and where (FR-H3)."""

    model_config = _FROZEN

    decision: Decision
    comment: str | None
    channel: DecisionChannel
    decided_by: str
    decided_at: datetime


class ApprovalRecord(BaseModel):
    """An approval as the inbox shows it: the AI-prepared packet (masked), the request, the decision."""

    model_config = _FROZEN

    id: str
    run_id: str
    task_id: str
    hire_id: str
    kind: ApprovalKind
    rule: str | None
    status: ApprovalStatus
    approver_id: str | None
    approver_role: str | None
    title: str
    items: list[str]
    packet: ApprovalPacket | None
    requested_at: datetime
    sla_hours: int | None
    decision: ApprovalDecision | None


class ApprovalEnvelope(Envelope[ApprovalRecord]):
    """One approval."""


class ApprovalListEnvelope(Envelope[list[ApprovalRecord]]):
    """A page of approvals, newest first."""


class ApprovalDecisionEnvelope(Envelope[ApprovalRecord]):
    """The approval after this caller's decision was recorded."""
