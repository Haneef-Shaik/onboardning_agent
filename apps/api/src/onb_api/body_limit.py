"""Bounded request bodies on `/templates` (TPL-2; security review H1).

A template body is parsed and then validated in full, so an unbounded body would let one request hold
the worker's memory and CPU. `BodyLimitMiddleware` reads a `/templates` request body itself, up to
`TEMPLATE_BODY_LIMIT` bytes. A declared `Content-Length` over the limit is refused before reading, and
a streamed body is refused the moment it passes the limit. Either way the answer is 413 in the
envelope. A body within the limit is handed to the app unchanged.

The limit fits the largest valid template: 100 tasks with 16 000 characters of inputs each, plus
their other fields.
"""

from __future__ import annotations

from typing import Final

from onb_contracts import ErrorCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from onb_api.errors import error_response

__all__ = ["TEMPLATE_BODY_LIMIT", "BodyLimitMiddleware"]

TEMPLATE_BODY_LIMIT: Final = 4 * 1024 * 1024
_LIMITED_PREFIX: Final = "/templates"
_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})


def _limited(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    return (path == _LIMITED_PREFIX or path.startswith(_LIMITED_PREFIX + "/")) and scope.get("method") in _BODY_METHODS


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limit: int = TEMPLATE_BODY_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _limited(scope):
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.limit:
            await error_response(ErrorCode.PAYLOAD_TOO_LARGE)(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, _replay([message], receive), send)  # a disconnect: let the app see it
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await error_response(ErrorCode.PAYLOAD_TOO_LARGE)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        whole: Message = {"type": "http.request", "body": bytes(body), "more_body": False}
        await self.app(scope, _replay([whole], receive), send)


def _replay(messages: list[Message], then: Receive) -> Receive:
    """`messages` first, then the client's own receive (so a later disconnect still reaches the app)."""
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return await then()

    return receive
