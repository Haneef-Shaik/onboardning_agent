"""The port between the chat service and the Run Assistant agent (AG-1).

The service never builds an agent itself: it hands one `AssistantTurn` to a `RunAssistant` and gets the
agent's validated `AssistantAnswer` back. The production implementation runs the registered
`run_assistant` through `onb_agents.runtime` (see `onb_api.chat.agent`); tests use a stub.
`message` is already redacted when it gets here (PRD §7.10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

__all__ = ["AssistantAnswer", "AssistantProposal", "AssistantTurn", "RunAssistant"]


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    org_id: str
    run_id: str
    user_id: str
    role: str
    message: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class AssistantProposal:
    type: str
    run_id: str
    task_id: str | None


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    status: Literal["ok", "refused", "needs_human"]
    answer: str
    citations: Sequence[tuple[str, str]] = field(default_factory=tuple)  # (run_id, task_id)
    proposal: AssistantProposal | None = None


class RunAssistant(Protocol):
    async def answer(self, turn: AssistantTurn) -> AssistantAnswer: ...
