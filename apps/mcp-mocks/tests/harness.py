"""An MCP client talking streamable HTTP to the real mcp-mocks ASGI app, in process.

Like the hub, the client signs a fresh call context for every `tools/call`, naming that tool and
the `mcp-mocks` audience.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from onb_mcp_mocks.server import AUDIENCE, build_server
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mongo.call_context import HEADER, CallContext, sign_call_context

KEY = "unit-test-mocks-context-key-0123456789abcdef"
CTX_A = CallContext(
    org_id="org_a",
    agent="provisioning_agent",
    run_id="run_1",
    tool="create_bitbucket_account",
    read=frozenset(),
    write=frozenset(),
)
TASK_1 = "65f0a1b2c3d4e5f6a7b8c9d0"
TASK_2 = "65f0a1b2c3d4e5f6a7b8c9d1"
HIRE_1 = "65f0aaaabbbbccccddddeeee"
BITBUCKET_ARGS: dict[str, Any] = {"task_id": TASK_1, "hire_id": HIRE_1, "workspace": "acme-eng", "groups": ["backend"]}
LAPTOP_ARGS: dict[str, Any] = {"task_id": TASK_2, "hire_id": HIRE_1, "profile": "engineering", "office": "bangalore"}


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def signing_hook(ctx: CallContext, key: str, *, audience: str = AUDIENCE) -> Callable[[httpx.Request], Awaitable[None]]:
    """An httpx request hook that adds a context for the tool each `tools/call` names."""

    async def hook(request: httpx.Request) -> None:
        body = json.loads(request.content or b"{}")
        if isinstance(body, dict) and body.get("method") == "tools/call":
            tool = body["params"]["name"]
            request.headers[HEADER] = sign_call_context(dataclasses.replace(ctx, tool=tool), key, audience=audience)

    return hook


@asynccontextmanager
async def mcp_session(
    store: InMemoryIdempotencyStore, *, header: str | None, ctx: CallContext, audience: str
) -> AsyncIterator[ClientSession]:
    server = build_server(store, KEY)
    app = server.streamable_http_app()
    if header is None:
        options: dict[str, Any] = {"event_hooks": {"request": [signing_hook(ctx, KEY, audience=audience)]}}
    else:
        options = {"headers": {HEADER: header}} if header else {}
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://mcp-mocks", **options) as http,
            streamable_http_client("http://mcp-mocks/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


def call[T](
    store: InMemoryIdempotencyStore,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    header: str | None = None,
    ctx: CallContext = CTX_A,
    audience: str = AUDIENCE,
) -> T:
    """Run `fn` in a client session. By default each call carries a context for `ctx` naming the
    called tool; `header` replaces that with a fixed header value ("" sends none)."""

    async def go() -> T:
        async with mcp_session(store, header=header, ctx=ctx, audience=audience) as session:
            return await fn(session)

    return run(go())
