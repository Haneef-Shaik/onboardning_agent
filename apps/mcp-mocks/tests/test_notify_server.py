"""The notify server over streamable HTTP: a hub-signed context for `mcp-notify`, the grant as a header."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from onb_agents.runtime.task import GRANT_HEADER as AGENT_GRANT_HEADER
from onb_mcp_mocks.notify.server import AUDIENCE, GRANT_HEADER, SPAN_PREFIX, build_notify_server
from onb_mcp_mongo.call_context import HEADER, CallContext, sign_call_context
from onb_telemetry.testing import CapturedTelemetry

from .harness import run
from .notify_world import World, build_world

KEY = "unit-test-notify-context-key-0123456789abcdef"
CTX = CallContext(
    org_id="org_acme",
    agent="templates_agent",
    run_id="run_acme_001",
    tool="notify_send",
    read=frozenset(),
    write=frozenset(),
)


@pytest.fixture
def world() -> World:
    return build_world()


@asynccontextmanager
async def session(world: World, headers: dict[str, str]) -> AsyncIterator[ClientSession]:
    server = build_notify_server(world.sink, KEY)
    app = server.streamable_http_app()
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://mcp-notify", headers=headers) as http,
            streamable_http_client("http://mcp-notify/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            yield client


def with_session[T](world: World, fn: Callable[[ClientSession], Awaitable[T]], headers: dict[str, str]) -> T:
    async def go() -> T:
        async with session(world, headers) as client:
            return await fn(client)

    return run(go())


def headers_for(
    grant: str | None, *, ctx: CallContext = CTX, key: str = KEY, audience: str = AUDIENCE
) -> dict[str, str]:
    headers = {HEADER: sign_call_context(ctx, key, audience=audience)}
    if grant is not None:
        headers[GRANT_HEADER] = grant
    return headers


def send(world: World, args: dict[str, Any], headers: dict[str, str]) -> CallToolResult:
    async def go(client: ClientSession) -> CallToolResult:
        return await client.call_tool("notify_send", args)

    return with_session(world, go, headers)


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def test_the_grant_header_is_the_one_the_agent_runtime_sends() -> None:
    assert AGENT_GRANT_HEADER.lower() == GRANT_HEADER


def test_the_server_lists_notify_send_with_the_shared_contract(world: World) -> None:
    async def go(client: ClientSession) -> Any:
        return await client.list_tools()

    [tool] = with_session(world, go, {}).tools
    assert tool.name == "notify_send"
    assert tool.inputSchema["additionalProperties"] is False
    assert set(tool.inputSchema["properties"]) == {"channel", "recipient_token", "recipient_user_id", "body"}
    assert tool.outputSchema is not None


def test_a_telegram_send_with_context_and_grant_is_sent_and_stored_masked(
    world: World, telemetry: CapturedTelemetry
) -> None:
    result = send(world, world.telegram(), headers_for(world.grant()))
    assert result.isError is False, text(result)
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "sent"
    [stored] = world.db.raw("mock_outbox")
    assert stored["recipient"]["masked"] == "98XXXXXX34"
    [span] = [s for s in telemetry.spans() if s.name == SPAN_PREFIX + "notify_send"]
    attrs = dict(span.attributes or {})
    assert (attrs["outcome"], attrs["org_id"], attrs["notify.channel"]) == ("ok", "org_acme", "telegram")
    assert "9876501234" not in json.dumps(attrs)


def test_a_rejected_grant_is_a_refusal_naming_the_reason_never_an_internal_error(
    world: World, telemetry: CapturedTelemetry
) -> None:
    result = send(world, world.telegram("other"), headers_for(world.grant()))
    assert result.isError is True
    assert text(result) == "grant_rejected (token_not_bound): nothing was sent"
    assert world.db.raw("mock_outbox") == []
    [span] = [s for s in telemetry.spans() if s.name == SPAN_PREFIX + "notify_send"]
    assert dict(span.attributes or {})["denial.reason"] == "grant_rejected:token_not_bound"
    assert [e.name for e in span.events] == ["guardrail.outcome"]


def test_no_grant_header_is_a_rejected_grant(world: World) -> None:
    result = send(world, world.telegram(), headers_for(None))
    assert text(result) == "grant_rejected (malformed): nothing was sent"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {HEADER: sign_call_context(CTX, KEY, audience="mcp-mocks")},
        {HEADER: sign_call_context(CTX, KEY, audience="mcp-mongo")},
        {HEADER: sign_call_context(CTX, "another-key-that-is-long-enough-0123456789", audience=AUDIENCE)},
        {HEADER: sign_call_context(CTX, KEY)},
    ],
    ids=["no-context", "mocks-audience", "mongo-audience", "forged-key", "default-audience"],
)
def test_without_a_context_for_mcp_notify_nothing_runs(world: World, headers: dict[str, str]) -> None:
    result = send(world, world.telegram(), {**headers, GRANT_HEADER: world.grant()})
    assert result.isError is True
    assert world.db.raw("mock_outbox") == []
    assert world.db.raw("pii_grant_jti") == []


def test_a_context_naming_another_tool_is_refused(world: World) -> None:
    other = CallContext(
        org_id="org_acme", agent="templates_agent", run_id="r", tool="find", read=frozenset(), write=frozenset()
    )
    result = send(world, world.telegram(), headers_for(world.grant(), ctx=other))
    assert text(result) == "the call context names another tool"


@pytest.mark.parametrize(
    "args",
    [
        {"channel": "telegram", "recipient_token": "ptk_x", "body": "hi", "org_id": "org_globex"},
        {"channel": "telegram", "body": "hi"},
        {"channel": "email", "recipient_token": "ptk_x", "body": "hi"},
    ],
    ids=["org-id", "no-recipient", "email"],
)
def test_bad_arguments_are_refused_without_echoing_values(world: World, args: dict[str, Any]) -> None:
    result = send(world, args, headers_for(world.grant()))
    assert result.isError is True
    assert text(result).startswith("invalid arguments")
    assert "org_globex" not in text(result)
    assert world.db.raw("pii_grant_jti") == []


def test_in_app_needs_no_grant(world: World) -> None:
    result = send(
        world, {"channel": "in_app", "recipient_user_id": "u_acme_hr1", "body": "Welcome sent."}, headers_for(None)
    )
    assert result.isError is False, text(result)
    assert len(world.db.raw("notifications")) == 1


def test_a_store_failure_is_an_internal_error(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(world.sink, "send", broken)
    result = send(world, world.telegram(), headers_for(world.grant()))
    assert text(result) == "notify_send: internal error"
