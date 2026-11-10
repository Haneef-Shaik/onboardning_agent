"""An MCP client talking streamable HTTP to the real mcp-mongo ASGI app, in process.

Like the hub, the client signs a fresh call context for every `tools/call`, naming that tool.
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
from onb_mcp_mongo.call_context import HEADER, CallContext, sign_call_context
from onb_mcp_mongo.server import build_server

from .fakes import FakeDatabase, make_store

KEY = "unit-test-call-context-key-0123456789abcdef"
CTX_A = CallContext(
    org_id="org_a",
    agent="db_agent",
    run_id="run_1",
    tool="find",
    read=frozenset({"new_hires"}),
    write=frozenset({"run_tasks"}),
)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def signing_hook(ctx: CallContext, key: str) -> Callable[[httpx.Request], Awaitable[None]]:
    """An httpx request hook that adds a context for the tool each `tools/call` names."""

    async def hook(request: httpx.Request) -> None:
        body = json.loads(request.content or b"{}")
        if isinstance(body, dict) and body.get("method") == "tools/call":
            tool = body["params"]["name"]
            request.headers[HEADER] = sign_call_context(dataclasses.replace(ctx, tool=tool), key)

    return hook


@asynccontextmanager
async def mcp_session(database: FakeDatabase, *, header: str | None, ctx: CallContext) -> AsyncIterator[ClientSession]:
    server = build_server(make_store(database), KEY)
    app = server.streamable_http_app()
    if header is None:
        options: dict[str, Any] = {"event_hooks": {"request": [signing_hook(ctx, KEY)]}}
    else:
        options = {"headers": {HEADER: header}} if header else {}
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://mcp-mongo", **options) as http,
            streamable_http_client("http://mcp-mongo/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


async def in_session[T](
    database: FakeDatabase,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    header: str | None = None,
    ctx: CallContext = CTX_A,
) -> T:
    async with mcp_session(database, header=header, ctx=ctx) as session:
        return await fn(session)


def call[T](
    database: FakeDatabase,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    header: str | None = None,
    ctx: CallContext = CTX_A,
) -> T:
    """Run `fn` in a client session. By default each call carries a context for `ctx` naming the
    called tool; `header` replaces that with a fixed header value ("" sends none)."""
    return run(in_session(database, fn, header=header, ctx=ctx))
