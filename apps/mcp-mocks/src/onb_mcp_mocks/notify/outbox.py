"""The Telegram mock channel: "send" through MOCK-1's transport stub, keep a masked copy in `mock_outbox`.

A `mock_outbox` document (tenant collection, PRD §9):

    {_id, org_id, channel: "telegram", recipient: {token, masked}, text, buttons: [{text,
     callback_data, decision}], ref: {type, id} | null, audience_user_id, run_id, task_id, agent,
     grant_digest, args_digest, message_id, sent_at}

It never holds a raw value: the recipient is the vault token and its mask, and `text` is the agent's
body, which the hub has already scanned for raw PII (the body may not carry tokens either, so nothing
is ever substituted into it). `grant_digest` (SHA-256 of the grant) makes a repeated call with the
same grant and the same arguments a replay of the first send instead of a second one.

Buttons (Approve/Reject, for platform senders such as HIL-1) get opaque callback data, registered
in the global `telegram_callbacks` directory (`{_id: callback data, org_id, created_at}` only, ADR
0013), so the webhook can find the org of a press before any tenant scope exists.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts.notify import OUTBOX_COLLECTION, TELEGRAM_CALLBACKS_COLLECTION, Decision, new_callback_data
from onb_data import DataStore, current_tenant

from ..telegram import ChatId, InlineButton, SentMessage, TelegramTransport

__all__ = ["DecisionButton", "OutboxEntry", "TelegramMockChannel", "digest", "ensure_outbox_indexes"]

_ID_BYTES: Final = 12


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DecisionButton:
    text: str
    decision: Decision


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """What is stored about one send, besides the org and the transport's receipt."""

    recipient_token: str | None
    recipient_masked: str
    text: str
    run_id: str | None = None
    task_id: str | None = None
    agent: str | None = None
    ref: Mapping[str, str] | None = None
    audience_user_id: str | None = None  # a platform user the message is for (an approver); MANAGERs see only theirs
    grant_digest: str | None = None
    args_digest: str | None = None


async def ensure_outbox_indexes(store: DataStore) -> None:
    await store.ensure_index(OUTBOX_COLLECTION, [("org_id", 1), ("sent_at", -1)])
    await store.ensure_index(
        OUTBOX_COLLECTION,
        [("org_id", 1), ("grant_digest", 1)],
        unique=True,
        partial={"grant_digest": {"$exists": True}},
        name="org_grant_digest",
    )
    await store.ensure_index(OUTBOX_COLLECTION, [("org_id", 1), ("buttons.callback_data", 1)])


class TelegramMockChannel:
    """The `telegram` adapter. A real adapter later swaps the transport and nothing else (FR-N2)."""

    def __init__(self, store: DataStore, transport: TelegramTransport) -> None:
        self._store = store
        self._transport = transport

    async def send(
        self, chat_id: ChatId, entry: OutboxEntry, *, buttons: Sequence[DecisionButton] = ()
    ) -> tuple[SentMessage, dict[str, Any]]:
        """Send, then return the receipt and the outbox document to store (the caller stores it, so the
        write can commit with its event). Callback data for `buttons` is registered first."""
        stored_buttons = [
            {"text": b.text, "callback_data": new_callback_data(), "decision": b.decision.value} for b in buttons
        ]
        await self._register(stored_buttons)
        keyboard = [[InlineButton(b["text"], b["callback_data"]) for b in stored_buttons]] if stored_buttons else []
        sent = await self._transport.send_message(chat_id, entry.text, inline_keyboard=keyboard)
        document: dict[str, Any] = {
            "_id": secrets.token_hex(_ID_BYTES),
            "channel": "telegram",
            "recipient": {"token": entry.recipient_token, "masked": entry.recipient_masked},
            "text": entry.text,
            "buttons": stored_buttons,
            "ref": dict(entry.ref) if entry.ref is not None else None,
            "audience_user_id": entry.audience_user_id,
            "run_id": entry.run_id,
            "task_id": entry.task_id,
            "agent": entry.agent,
            "message_id": sent.message_id,
            "sent_at": _now(),
        }
        if entry.grant_digest is not None:
            document["grant_digest"] = entry.grant_digest
            document["args_digest"] = entry.args_digest
        return sent, document

    async def _register(self, buttons: Sequence[Mapping[str, str]]) -> None:
        if not buttons:
            return
        org_id = current_tenant().org_id
        at = _now()
        await self._store.shared(TELEGRAM_CALLBACKS_COLLECTION).insert_many(
            [{"_id": b["callback_data"], "org_id": org_id, "created_at": at} for b in buttons]
        )

    async def store(self, document: Mapping[str, Any]) -> None:
        await self._store.tenant(OUTBOX_COLLECTION).insert_one(document)

    async def find_replay(self, grant_digest: str) -> dict[str, Any] | None:
        return await self._store.tenant(OUTBOX_COLLECTION).find_one({"grant_digest": grant_digest})
