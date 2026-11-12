"""Call one of the app's own routes in-process, as an outside client would (UI-6, ADR 0026).

The mock outbox's Approve/Reject buttons must reach `POST /webhooks/telegram`, the endpoint a real Telegram
callback hits (PRD FR-N2), without the browser ever holding the webhook secret. The press route builds the
Telegram `Update` and sends it here: one plain ASGI HTTP request through the whole app (its middleware, the
webhook's secret check, its body validation), with no network, no extra dependency and no configured URL.

The request runs in an **empty context**: nothing of the caller's request (its tenant scope, its span) leaks
into the webhook's, exactly as with a request that arrives from Telegram.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Mapping
from typing import Any, Final

__all__ = ["RelayError", "post_json"]

_SERVER: Final = ("onb-api.internal", 80)
_CLIENT: Final = ("127.0.0.1", 0)


class RelayError(RuntimeError):
    """The in-process call did not answer 2xx (the status is in the message, never the body)."""


async def _call(app: Any, scope: dict[str, Any], body: bytes) -> int:
    done = asyncio.Event()
    delivered = False
    status: list[int] = []

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await done.wait()  # nothing more to read: the "client" goes away once the response is complete
        return {"type": "http.disconnect"}

    async def send(message: Mapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(int(message["status"]))
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            done.set()

    try:
        await app(scope, receive, send)
    finally:
        done.set()
    if not status:
        raise RelayError("the route sent no response")
    return status[0]


async def post_json(
    app: Any, path: str, payload: Mapping[str, Any], *, headers: Mapping[str, str], root_path: str = ""
) -> int:
    """POST `payload` as JSON to `path` of `app`; returns the status, `RelayError` unless it is 2xx."""
    body = json.dumps(payload).encode()
    raw_headers = [
        (b"host", _SERVER[0].encode()),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *((name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()),
    ]
    full_path = root_path + path
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": full_path,
        "raw_path": full_path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": raw_headers,
        "client": _CLIENT,
        "server": _SERVER,
        "state": {},
    }
    task = asyncio.get_running_loop().create_task(_call(app, scope, body), context=contextvars.Context())
    status = await task
    if not 200 <= status < 300:
        raise RelayError(f"{path} answered {status}")
    return status
