"""Run Assistant chat contracts (PRD FR-C1, FR-C2, U6, §7.6 Conversation, §10 Chat; CHAT-1).

`POST /runs/{id}/chat` takes one question about a run and answers over server-sent events: one `message`
event (the assistant's reply, with the tasks it cites and, when it proposes a change, the proposal) and one
`end`. Chat never changes a run. When the assistant proposes a retry the reply carries a `ProposedAction`
with a `proposal_id`; the retry happens only when HR calls `POST /runs/{id}/chat/confirm` with that id.
`GET /runs/{id}/chat` returns the stored conversation. Stored messages hold redaction markers, never
the raw values of personal data (PRD §7.10).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from onb_contracts.envelope import Envelope
from onb_contracts.inputs import RequestModel
from onb_contracts.runs import TASK_ID_PATTERN, RunRecord

__all__ = [
    "MAX_CHAT_MESSAGE_LENGTH",
    "PROPOSAL_ID_PATTERN",
    "ChatConfirmRequest",
    "ChatEnvelope",
    "ChatMessageRecord",
    "ChatReply",
    "ChatRequest",
    "Citation",
    "ConfirmEnvelope",
    "ProposalRecord",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

MAX_CHAT_MESSAGE_LENGTH: Final = 2000
PROPOSAL_ID_PATTERN: Final = r"^prop_[0-9a-f]{24}$"


class ChatRequest(RequestModel):
    """`POST /runs/{id}/chat`: one question. Free text; it is redacted before it is stored or sent on."""

    message: Annotated[str, Field(min_length=1, max_length=MAX_CHAT_MESSAGE_LENGTH)]


class ChatConfirmRequest(RequestModel):
    """`POST /runs/{id}/chat/confirm`: HR confirms one proposal the assistant made in this run's chat."""

    proposal_id: Annotated[str, Field(pattern=PROPOSAL_ID_PATTERN)]


class Citation(BaseModel):
    model_config = _FROZEN

    run_id: str
    task_id: Annotated[str, Field(pattern=TASK_ID_PATTERN)]


class ProposalRecord(BaseModel):
    """A change the assistant proposed. Nothing has happened yet: `pending` until HR confirms it."""

    model_config = _FROZEN

    proposal_id: Annotated[str, Field(pattern=PROPOSAL_ID_PATTERN)]
    type: Literal["retry_task"]
    run_id: str
    task_id: Annotated[str, Field(pattern=TASK_ID_PATTERN)]
    requires_confirmation: Literal[True] = True
    status: Literal["pending", "confirmed", "failed"]
    expires_at: datetime


class ChatMessageRecord(BaseModel):
    model_config = _FROZEN

    id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["ok", "refused", "needs_human"] | None = None
    citations: list[Citation] = Field(default_factory=list)
    proposal: ProposalRecord | None = None
    created_at: datetime


class ChatReply(BaseModel):
    """The one `message` event of a chat answer."""

    model_config = _FROZEN

    message: ChatMessageRecord


class ChatEnvelope(Envelope[list[ChatMessageRecord]]):
    """The stored conversation about one run, oldest first."""


class ConfirmEnvelope(Envelope[RunRecord]):
    """The run after the confirmed retry."""
