"""Notification contracts (PRD FR-N1, FR-N2, FR-H2, §7.10 sink flow, §10 Mock channels; NOTIF-1).

One module, so the agent tool, the hub, the eval harness and the API can never disagree:

* **The agent tool** is `notify.notify_send` (hub name `notify__notify_send`, REG-1's
  `templates_agent` manifest, the eval cases' `notify_send`). Arguments: `channel`, the recipient and
  `body`. `telegram` takes a vault token (`recipient_token`, a mobile resolved only at the sink with
  the orchestrator's grant); `in_app` takes a platform user id (`recipient_user_id`). No other
  argument exists: tenant scope, run and task come from the hub's signed context and the grant.
* **The webhook** takes Telegram's documented `Update` with a `callback_query`
  (https://core.telegram.org/bots/api#update, #callbackquery). Telegram adds fields over time, so
  unknown fields are ignored, never refused.
* **Callback data** is `ob1:<22 url-safe characters>`: an opaque random id (128 bits) that names one
  button of one outbox message. It says nothing about the org, the decision or the record; the
  platform looks all three up (ADR 0013).
* **Events** the notification flow publishes or consumes through the EVT-1 bus.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onb_contracts.envelope import Envelope

__all__ = [
    "CALLBACK_DATA_PATTERN",
    "CALLBACK_PREFIX",
    "CHANGE_REQUEST_DECIDED",
    "DECISION_RECEIVED",
    "IN_APP_CONSUMER",
    "MAX_BODY_CHARS",
    "MAX_OUTBOX_LIMIT",
    "NOTIFICATIONS_COLLECTION",
    "NOTIFICATION_SENT",
    "NOTIFY_SEND",
    "NOTIFY_SERVER",
    "NOTIFY_SUBSCRIPTIONS",
    "OUTBOX_COLLECTION",
    "PTK_PATTERN",
    "RUN_BLOCKED",
    "SINK_REJECTED",
    "TELEGRAM_AUTH_HEADER",
    "TELEGRAM_CALLBACKS_COLLECTION",
    "TELEGRAM_SINK",
    "CallbackQuery",
    "Channel",
    "Decision",
    "MockOutboxEnvelope",
    "MockOutboxMessage",
    "NotifySendArgs",
    "NotifySendResult",
    "OutboxButton",
    "TelegramChat",
    "TelegramMessage",
    "TelegramUpdate",
    "TelegramUser",
    "WebhookAck",
    "WebhookAckEnvelope",
    "new_callback_data",
    "parse_callback_data",
]

NOTIFY_SERVER: Final = "notify"
NOTIFY_SEND: Final = "notify_send"
TELEGRAM_SINK: Final = "notify.telegram"  # the sink id grants and the field → sink policy name

OUTBOX_COLLECTION: Final = "mock_outbox"
NOTIFICATIONS_COLLECTION: Final = "notifications"
TELEGRAM_CALLBACKS_COLLECTION: Final = "telegram_callbacks"

# Bot API setWebhook(secret_token): Telegram sends it back in this header on every update.
TELEGRAM_AUTH_HEADER: Final = "X-Telegram-Bot-Api-Secret-Token"

MAX_BODY_CHARS: Final = 4000
MAX_OUTBOX_LIMIT: Final = 100
PTK_PATTERN: Final = r"^ptk_[A-Za-z0-9_]{1,124}$"
USER_ID_PATTERN: Final = r"^[A-Za-z0-9_.:-]{1,128}$"

CALLBACK_PREFIX: Final = "ob1:"
CALLBACK_DATA_PATTERN: Final = r"^ob1:[A-Za-z0-9_-]{22}$"
_CALLBACK: Final = re.compile(CALLBACK_DATA_PATTERN)

# --- events (EVT-1 bus) -------------------------------------------------------------------------------
NOTIFICATION_SENT: Final = "notification.sent"
"""Published by the sink with the outbox write, in one transaction: `{outbox_id, channel, run_id, task_id}`."""
SINK_REJECTED: Final = "task.sink_rejected"
"""Published by the sink when a grant is refused: `{run_id, agent, task_id?, sink, reason}`. The
orchestrator (RUN-3) subscribes and fails the task; nothing was sent."""
DECISION_RECEIVED: Final = "decision.received"
"""Published by `POST /webhooks/telegram` for an Approve/Reject press: `{ref_type, ref_id, decision,
channel, outbox_id, telegram_user_id, callback_query_id}`. HIL-1 subscribes (first decision wins)."""
CHANGE_REQUEST_DECIDED: Final = "change_request.decided"
"""Published by TB-2: `{change_request_id, requested_by, decision}`; the requester is told in-app."""
RUN_BLOCKED: Final = "run.blocked"
"""Published by RUN-2: `{run_id, task_id}`; the org's HR and HR_ADMIN users are told in-app."""

IN_APP_CONSUMER: Final = "notify.in_app"
NOTIFY_SUBSCRIPTIONS: Final[dict[str, list[str]]] = {
    CHANGE_REQUEST_DECIDED: [IN_APP_CONSUMER],
    RUN_BLOCKED: [IN_APP_CONSUMER],
}
"""The notification consumers, by event type: every publisher of these events builds its bus with them."""

_FROZEN = ConfigDict(frozen=True, extra="forbid")
_TELEGRAM = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class Channel(StrEnum):
    TELEGRAM = "telegram"
    IN_APP = "in_app"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


# --- the agent tool -----------------------------------------------------------------------------------


class NotifySendArgs(BaseModel):
    """`notify_send` arguments. `telegram` needs `recipient_token` (a vault token for a mobile);
    `in_app` needs `recipient_user_id`. The other recipient field must be absent."""

    model_config = _FROZEN

    channel: Channel
    recipient_token: str | None = Field(default=None, pattern=PTK_PATTERN)
    recipient_user_id: str | None = Field(default=None, pattern=USER_ID_PATTERN)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)

    @model_validator(mode="after")
    def _one_recipient_for_the_channel(self) -> NotifySendArgs:
        if self.channel is Channel.TELEGRAM and (self.recipient_token is None or self.recipient_user_id is not None):
            raise ValueError("telegram takes recipient_token (a vault token) and no recipient_user_id")
        if self.channel is Channel.IN_APP and (self.recipient_user_id is None or self.recipient_token is not None):
            raise ValueError("in_app takes recipient_user_id and no recipient_token")
        return self


class NotifySendResult(BaseModel):
    """What the agent learns: the message was sent (or was already sent, `replayed`). Never a value."""

    model_config = _FROZEN

    status: Literal["sent"]
    channel: Channel
    notification_id: str
    replayed: bool = False


# --- Telegram's callback contract -------------------------------------------------------------------------


class TelegramUser(BaseModel):
    """Bot API `User`; only `id` is used (as the decider's Telegram id). Names are never stored."""

    model_config = _TELEGRAM

    id: int
    is_bot: bool
    first_name: str


class TelegramChat(BaseModel):
    model_config = _TELEGRAM

    id: int
    type: str


class TelegramMessage(BaseModel):
    """Bot API `Message` (or `InaccessibleMessage`, whose `date` is 0): the message the button was on."""

    model_config = _TELEGRAM

    message_id: int
    date: int
    chat: TelegramChat


class CallbackQuery(BaseModel):
    """Bot API `CallbackQuery`: `id`, `from`, `chat_instance` always; `data` for a callback button."""

    model_config = _TELEGRAM

    id: str = Field(min_length=1, max_length=256)
    from_: TelegramUser = Field(alias="from")
    chat_instance: str
    message: TelegramMessage | None = None
    inline_message_id: str | None = None
    data: str | None = None
    game_short_name: str | None = None


class TelegramUpdate(BaseModel):
    """Bot API `Update`. Only `callback_query` updates mean anything here; others are acknowledged."""

    model_config = _TELEGRAM

    update_id: int
    callback_query: CallbackQuery | None = None


class WebhookAck(BaseModel):
    """The same answer for every accepted update, so the response tells a caller nothing."""

    model_config = _FROZEN

    ok: Literal[True] = True


class WebhookAckEnvelope(Envelope[WebhookAck]):
    pass


def new_callback_data() -> str:
    """A fresh button id: `ob1:` + 128 random bits (22 url-safe characters), 26 bytes in all."""
    return CALLBACK_PREFIX + secrets.token_urlsafe(16)


def parse_callback_data(data: str | None) -> str | None:
    """The button id in `data`, or None when it is not ours."""
    if data is None or not _CALLBACK.fullmatch(data):
        return None
    return data


# --- GET /mock-outbox -----------------------------------------------------------------------------------


class OutboxButton(BaseModel):
    model_config = _FROZEN

    text: str
    callback_data: str
    decision: Decision


class MockOutboxMessage(BaseModel):
    """One message the Telegram mock "sent": the recipient masked, never raw (PRD §7.10)."""

    model_config = _FROZEN

    id: str
    channel: Literal["telegram"]
    recipient_masked: str
    text: str
    buttons: list[OutboxButton]
    run_id: str | None
    task_id: str | None
    message_id: int
    sent_at: datetime


class MockOutboxEnvelope(Envelope[list[MockOutboxMessage]]):
    pass
