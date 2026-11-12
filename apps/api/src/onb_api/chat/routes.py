"""Run Assistant chat over HTTP (PRD FR-C1, FR-C2, §10 Chat; CHAT-1).

| Route | Roles |
|---|---|
| `POST /runs/{id}/chat` (SSE) | any signed-in role; a MANAGER only for hires they manage |
| `GET /runs/{id}/chat` | same; the caller's own conversation |
| `POST /runs/{id}/chat/confirm` | HR, HR_ADMIN |

A run the caller may not see is 404, like one that does not exist, before the assistant is called. The
chat answers `text/event-stream`: one `message` event (`ChatReply`), then `end`. The answer is computed
before the stream starts, so a failure is an ordinary error envelope (503), never a half stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from onb_contracts import ErrorCode
from onb_contracts.auth import Role
from onb_contracts.chat import (
    ChatConfirmRequest,
    ChatEnvelope,
    ChatMessageRecord,
    ChatReply,
    ChatRequest,
    ConfirmEnvelope,
)
from onb_contracts.envelope import Envelope
from onb_contracts.runs import RunRecord

from onb_api.auth.dependencies import CurrentPrincipal, require_role
from onb_api.auth.tokens import Principal
from onb_api.chat.service import ChatService
from onb_api.errors import AppError
from onb_api.responses import respond
from onb_api.runs.stream import sse

__all__ = ["chat_service", "router"]

router = APIRouter(tags=["chat"])

Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]
RunId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def chat_service(request: Request) -> ChatService:
    service: ChatService | None = getattr(request.app.state, "chat", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Chat = Annotated[ChatService, Depends(chat_service)]


@router.post(
    "/runs/{run_id}/chat",
    operation_id="chatAboutRun",
    response_class=StreamingResponse,
    responses={200: {"description": "Server-sent events: message, end.", "content": {"text/event-stream": {}}}},
)
async def chat_about_run(
    principal: CurrentPrincipal, chat: Chat, run_id: RunId, body: ChatRequest
) -> StreamingResponse:
    reply = ChatReply(message=await chat.chat(principal, run_id, body.message))

    async def events() -> AsyncIterator[str]:
        yield sse("message", reply.model_dump(mode="json"))
        yield sse("end", {"status": "ok"})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/runs/{run_id}/chat", response_model=ChatEnvelope, operation_id="getRunChat")
async def get_run_chat(principal: CurrentPrincipal, chat: Chat, run_id: RunId) -> Envelope[list[ChatMessageRecord]]:
    return respond(await chat.history(principal, run_id))


@router.post("/runs/{run_id}/chat/confirm", response_model=ConfirmEnvelope, operation_id="confirmRunChatAction")
async def confirm_chat_action(
    principal: Hr, chat: Chat, run_id: RunId, body: ChatConfirmRequest
) -> Envelope[RunRecord]:
    return respond(await chat.confirm(principal, run_id, body.proposal_id))
