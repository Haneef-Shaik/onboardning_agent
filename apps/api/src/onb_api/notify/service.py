"""The mock channels behind `GET /mock-outbox` and `POST /webhooks/telegram` (PRD FR-N2, §10; NOTIF-1).

**Outbox.** What the Telegram mock "sent", newest first, in the caller's org (the token's). HR and
HR_ADMIN see the org's outbox; a MANAGER sees only messages addressed to them (`audience_user_id`,
an approver's own Approve/Reject requests). Every stored copy is masked at the sink; the reader adds
nothing and passes no stored field through unchecked.

**Webhook.** Telegram's callback contract: the secret token Telegram echoes in
`X-Telegram-Bot-Api-Secret-Token` (compared in constant time), then an `Update` whose
`callback_query.data` is one of our button ids. The button id's org comes from the global
`telegram_callbacks` directory (ADR 0013), never from the request; the decision comes from the stored
button, never from the request. A press publishes `decision.received` on the EVT-1 bus (ids and codes
only, not the presser's Telegram id, a personal identifier), keyed by Telegram's `callback_query.id`,
so Telegram's retries of one press publish once. Every accepted update gets the same answer, so a
caller learns nothing about which ids exist.
"""

from __future__ import annotations

import hmac
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from onb_contracts.auth import Role
from onb_contracts.notify import (
    DECISION_RECEIVED,
    NOTIFY_SUBSCRIPTIONS,
    OUTBOX_COLLECTION,
    TELEGRAM_CALLBACKS_COLLECTION,
    CallbackQuery,
    Decision,
    MockOutboxMessage,
    OutboxButton,
    TelegramUpdate,
    parse_callback_data,
)
from onb_data import DataStore, current_tenant, tenant_scope
from onb_eventbus import MongoEventBus

from onb_api.auth.tokens import Principal

__all__ = [
    "BusDecisions",
    "DecisionPublisher",
    "MockChannels",
    "OutboxPage",
    "WebhookSecretError",
    "check_webhook_secret",
]

_log = logging.getLogger("onb_api.notify")
# Bot API setWebhook(secret_token): 1-256 characters, A-Z, a-z, 0-9, _ and -.
_SECRET: Final = re.compile(r"[A-Za-z0-9_-]{32,256}")
_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class WebhookSecretError(ValueError):
    """`TELEGRAM_WEBHOOK_SECRET` is missing or malformed. The message never holds the value."""


def check_webhook_secret(secret: str) -> str:
    """At least 32 of Telegram's allowed characters (it allows 1-256; short ones are guessable)."""
    if not isinstance(secret, str) or not _SECRET.fullmatch(secret):
        raise WebhookSecretError("TELEGRAM_WEBHOOK_SECRET must be 32-256 characters of A-Z a-z 0-9 _ -")
    return secret


class DecisionPublisher(Protocol):
    async def publish(self, payload: Mapping[str, Any], *, key: str) -> bool:
        """Publish `decision.received` in the current org; False when the key was already used."""


class BusDecisions:
    """`DecisionPublisher` over the EVT-1 bus, with the platform's notification subscriptions."""

    def __init__(self, store: DataStore) -> None:
        self._bus = MongoEventBus(store, subscriptions=NOTIFY_SUBSCRIPTIONS)

    async def ensure_indexes(self) -> None:
        await self._bus.ensure_indexes()

    async def publish(self, payload: Mapping[str, Any], *, key: str) -> bool:
        result = await self._bus.publish(DECISION_RECEIVED, payload, idempotency_key=key)
        return not result.duplicate


@dataclass(frozen=True, slots=True)
class OutboxPage:
    messages: tuple[MockOutboxMessage, ...]
    total: int


def _id_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _message(doc: Mapping[str, Any]) -> MockOutboxMessage:
    recipient = doc.get("recipient") or {}
    return MockOutboxMessage(
        id=str(doc["_id"]),
        channel="telegram",
        recipient_masked=str(recipient.get("masked", "")),
        text=str(doc.get("text", "")),
        buttons=[
            OutboxButton(text=str(b["text"]), callback_data=str(b["callback_data"]), decision=Decision(b["decision"]))
            for b in doc.get("buttons") or []
        ],
        run_id=_id_or_none(doc.get("run_id")),
        task_id=_id_or_none(doc.get("task_id")),
        message_id=int(doc.get("message_id", 0)),
        sent_at=doc["sent_at"]
        if isinstance(doc.get("sent_at"), datetime)
        else datetime.fromisoformat(str(doc["sent_at"])),
    )


def _readable(doc: Mapping[str, Any]) -> MockOutboxMessage | None:
    """The message, or None (logged by id) for a document that is not an outbox copy: one bad record
    must not take the whole outbox down."""
    try:
        return _message(doc)
    except (KeyError, TypeError, ValueError):
        _log.warning("mock_outbox: skipped an unreadable document %s", doc.get("_id"))
        return None


class MockChannels:
    def __init__(self, store: DataStore, *, webhook_secret: str, decisions: DecisionPublisher) -> None:
        self._store = store
        self._secret = check_webhook_secret(webhook_secret).encode()
        self._decisions = decisions

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(OUTBOX_COLLECTION, [("org_id", 1), ("sent_at", -1)])
        ensure = getattr(self._decisions, "ensure_indexes", None)
        if ensure is not None:
            await ensure()

    # --- GET /mock-outbox -----------------------------------------------------------------------------

    async def list_outbox(self, principal: Principal, *, page: int, limit: int) -> OutboxPage:
        """One page of the caller's org outbox (the route runs in the token's tenant scope)."""
        query = {"audience_user_id": principal.user_id} if principal.role is Role.MANAGER else {}
        repo = self._store.tenant(OUTBOX_COLLECTION)
        docs = await repo.find(query, sort=[("sent_at", -1), ("_id", -1)], skip=(page - 1) * limit, limit=limit)
        return OutboxPage(tuple(m for m in map(_readable, docs) if m is not None), await repo.count(query))

    # --- POST /webhooks/telegram ----------------------------------------------------------------------

    def secret_matches(self, presented: str | None) -> bool:
        return presented is not None and hmac.compare_digest(presented.encode(), self._secret)

    async def handle(self, update: TelegramUpdate) -> None:
        """Publish the decision a press stands for, or do nothing; the answer is the same either way."""
        query = update.callback_query
        button_id = parse_callback_data(query.data if query is not None else None)
        if query is None or button_id is None:
            return
        entry = await self._store.shared(TELEGRAM_CALLBACKS_COLLECTION).find_one({"_id": button_id})
        org_id = _id_or_none(entry.get("org_id")) if entry is not None else None
        if org_id is None:
            return
        with tenant_scope(org_id):
            await self._decide(query, button_id)

    async def _decide(self, query: CallbackQuery, button_id: str) -> None:
        message = await self._store.tenant(OUTBOX_COLLECTION).find_one({"buttons.callback_data": button_id})
        if message is None:
            return
        button = next((b for b in message.get("buttons") or [] if b.get("callback_data") == button_id), None)
        if button is None:
            return
        ref = message.get("ref") or {}
        payload = {
            "ref_type": _id_or_none(ref.get("type")),
            "ref_id": _id_or_none(ref.get("id")),
            "decision": Decision(button["decision"]).value,
            "channel": "telegram",
            "outbox_id": str(message["_id"]),
            "callback_query_id": query.id,
        }
        published = await self._decisions.publish(payload, key=f"telegram:callback:{query.id}")
        _log.info(
            "telegram callback: org=%s outbox=%s decision=%s new=%s",
            current_tenant().org_id,
            payload["outbox_id"],
            payload["decision"],
            published,
        )
