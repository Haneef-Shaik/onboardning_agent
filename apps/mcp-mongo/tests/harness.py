"""An MCP client talking streamable HTTP to the real mcp-mongo ASGI app, in process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

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
    read=frozenset({"new_hires"}),
    write=frozenset({"run_tasks"}),
)

T = TypeVar("T")
Session = Callable[[Callable[[ClientSession], Awaitable[T]]], T]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@asynccontextmanager
async def mcp_session(database: FakeDatabase, header: str | None) -> AsyncIterator[ClientSession]:
    server = build_server(make_store(database), KEY)
    app = server.streamable_http_app()
    headers = {HEADER: header} if header is not None else {}
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://mcp-mongo", headers=headers) as http,
            streamable_http_client("http://mcp-mongo/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


def call[T](database: FakeDatabase, fn: Callable[[ClientSession], Awaitable[T]], *, header: str | None = None) -> T:
    """Run `fn` with a client session whose requests carry `header` (default: a context for org_a)."""
    token = sign_call_context(CTX_A, KEY) if header is None else header

    async def main() -> T:
        async with mcp_session(database, token or None) as session:
            return await fn(session)

    return run(main())
