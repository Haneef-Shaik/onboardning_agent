"""Mock channel routes (PRD §10 Mock channels, FR-N2; NOTIF-1).

| Route | Who |
|---|---|
| `GET /mock-outbox` | HR_ADMIN, HR (the org's outbox), MANAGER (messages addressed to them) |
| `POST /webhooks/telegram` | Telegram: the webhook secret token, no user token |

The webhook takes no bearer token, as Telegram sends none: it authenticates with
`X-Telegram-Bot-Api-Secret-Token`. A wrong or missing secret is 401 before the body is validated (a body
that is not JSON at all is 400 first: FastAPI reads it before the dependencies run). The org
of a press comes from the button id, through the callback directory (ADR 0013), never from the
request. Any update with the right secret answers `{ok: true}`, whatever it named.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from onb_contracts import Envelope, ErrorCode, PageMeta
from onb_contracts.auth import Role
from onb_contracts.notify import (
    MAX_OUTBOX_LIMIT,
    MockOutboxEnvelope,
    MockOutboxMessage,
    TelegramUpdate,
    WebhookAck,
    WebhookAckEnvelope,
)

from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.notify.service import MockChannels
from onb_api.responses import respond

__all__ = ["mock_channels", "router"]

router = APIRouter(tags=["mock channels"])

AnyRole = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR, Role.MANAGER))]
MAX_OUTBOX_PAGE = 100  # pages reach at most 100 x 100 messages deep


def mock_channels(request: Request) -> MockChannels:
    channels: MockChannels | None = request.app.state.notify
    if channels is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return channels


@router.get("/mock-outbox", response_model=MockOutboxEnvelope, operation_id="listMockOutbox")
async def list_mock_outbox(
    principal: AnyRole,
    channels: Annotated[MockChannels, Depends(mock_channels)],
    page: Annotated[int, Query(ge=1, le=MAX_OUTBOX_PAGE)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_OUTBOX_LIMIT)] = 50,
) -> Envelope[list[MockOutboxMessage]]:
    found = await channels.list_outbox(principal, page=page, limit=limit)
    return respond(list(found.messages), page=PageMeta(total=found.total, page=page, limit=limit))


def telegram_sender(
    request: Request,
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> MockChannels:
    """The webhook's authentication: Telegram's secret token. A dependency, so it is checked before the
    body is validated: a wrong secret is 401 for any JSON body."""
    channels = mock_channels(request)
    if not channels.secret_matches(secret):
        raise AppError(ErrorCode.UNAUTHENTICATED)
    return channels


@router.post("/webhooks/telegram", response_model=WebhookAckEnvelope, operation_id="telegramWebhook")
async def telegram_webhook(
    update: TelegramUpdate,
    channels: Annotated[MockChannels, Depends(telegram_sender)],
) -> Envelope[WebhookAck]:
    await channels.handle(update)
    return respond(WebhookAck())
