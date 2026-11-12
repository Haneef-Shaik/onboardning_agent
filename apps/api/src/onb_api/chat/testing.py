"""Test support for the chat: a scripted `RunAssistant`. Test-only; production code never imports it."""

from __future__ import annotations

from collections.abc import Callable

from onb_api.chat.assistant import AssistantAnswer, AssistantTurn

__all__ = ["StubAssistant"]


class StubAssistant:
    """Answers with `reply(turn)` (default: an `ok` answer that echoes the redacted question) and records
    every turn it was given, so a test can show what the assistant saw, or that it was never called."""

    def __init__(self, reply: Callable[[AssistantTurn], AssistantAnswer] | None = None) -> None:
        self.turns: list[AssistantTurn] = []
        self.reply = reply or (lambda turn: AssistantAnswer(status="ok", answer=f"You asked: {turn.message}"))

    async def answer(self, turn: AssistantTurn) -> AssistantAnswer:
        self.turns.append(turn)
        return self.reply(turn)
