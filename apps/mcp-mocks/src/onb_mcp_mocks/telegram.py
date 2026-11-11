"""The Telegram transport stub (PRD FR-N1, §6.6, §7.10): what NOTIF-1's `telegram_mock` sink sends through.

`TelegramTransport` is the seam: the sink resolves the vault token, substitutes it, calls
`send_message`, and stores only a masked copy in `mock_outbox` itself. A real adapter later
implements the same protocol over the Bot API; only the adapter changes (FR-N2).

`TelegramMockTransport` calls nothing. It checks the Bot API's limits (text 1..4096 characters,
inline-button callback data 1..64 bytes), answers with a Bot-API-shaped receipt, and keeps **no copy**
of the recipient or the text: they are the resolved, raw values, and the sink owns what is stored.
It is not an MCP tool: raw values must never travel through the hub to reach it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "MAX_CALLBACK_DATA_BYTES",
    "MAX_TEXT_CHARS",
    "ChatId",
    "InlineButton",
    "SentMessage",
    "TelegramMockTransport",
    "TelegramTransport",
    "TransportRefusedError",
]

MAX_TEXT_CHARS = 4096  # Bot API sendMessage: 1-4096 characters after entity parsing
MAX_CALLBACK_DATA_BYTES = 64  # Bot API InlineKeyboardButton.callback_data: 1-64 bytes
MAX_USERNAME_CHARS = 33  # "@" + 5..32 characters

ChatId = int | str


class TransportRefusedError(ValueError):
    """The message breaks a Bot API limit. The message never holds the recipient or the text."""


@dataclass(frozen=True, slots=True)
class InlineButton:
    """An inline-keyboard button whose press sends `callback_data` to the webhook (`callback_query`)."""

    text: str
    callback_data: str

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("a button needs text")
        if not 1 <= len(self.callback_data.encode()) <= MAX_CALLBACK_DATA_BYTES:
            raise ValueError(f"callback_data must be 1-{MAX_CALLBACK_DATA_BYTES} bytes")


@dataclass(frozen=True, slots=True)
class SentMessage:
    """The receipt: what the Bot API returns about a sent message, minus the echoed chat and text."""

    message_id: int
    date: int

    def to_bot_api(self) -> dict[str, Any]:
        return {"ok": True, "result": {"message_id": self.message_id, "date": self.date}}


@runtime_checkable
class TelegramTransport(Protocol):
    async def send_message(
        self, chat_id: ChatId, text: str, *, inline_keyboard: Sequence[Sequence[InlineButton]] = ()
    ) -> SentMessage: ...


def _valid_chat(chat_id: object) -> bool:
    if isinstance(chat_id, bool):
        return False
    if isinstance(chat_id, int):
        return chat_id > 0
    if isinstance(chat_id, str):
        name = chat_id[1:]
        return chat_id.startswith("@") and 5 <= len(name) < MAX_USERNAME_CHARS and name.replace("_", "").isalnum()
    return False


class TelegramMockTransport:
    """Sends nowhere. Message ids count up from 1 per transport, like a chat's ids."""

    def __init__(self, *, clock: Callable[[], int] | None = None) -> None:
        self._clock = clock or (lambda: int(time.time()))
        self._sent = 0

    async def send_message(
        self, chat_id: ChatId, text: str, *, inline_keyboard: Sequence[Sequence[InlineButton]] = ()
    ) -> SentMessage:
        if not _valid_chat(chat_id):
            raise TransportRefusedError("chat_id must be a positive id or an @username")
        if not 1 <= len(text) <= MAX_TEXT_CHARS:
            raise TransportRefusedError(f"text must be 1-{MAX_TEXT_CHARS} characters")
        if not all(isinstance(b, InlineButton) for row in inline_keyboard for b in row):
            raise TransportRefusedError("inline_keyboard holds InlineButton rows only")
        self._sent += 1
        return SentMessage(message_id=self._sent, date=self._clock())

    @property
    def sent(self) -> int:
        """How many messages were accepted."""
        return self._sent
