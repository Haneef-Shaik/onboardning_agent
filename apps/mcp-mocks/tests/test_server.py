"""The mcp-mocks server over streamable HTTP: reached only with a hub-signed context for `mcp-mocks`."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from onb_mcp_mocks.contracts import TOOL_NAMES
from onb_mcp_mocks.server import SPAN_PREFIX, build_server
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mongo.call_context import sign_call_context
from onb_telemetry.testing import CapturedTelemetry

from .harness import BITBUCKET_ARGS, CTX_A, KEY, LAPTOP_ARGS, call


def _text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def _call(store: InMemoryIdempotencyStore, name: str, args: dict[str, Any], **kw: Any) -> CallToolResult:
    async def go(session: ClientSession) -> CallToolResult:
        return await session.call_tool(name, args)

    return call(store, go, **kw)


def test_the_server_lists_the_two_mock_tools_with_closed_schemas() -> None:
    async def go(session: ClientSession) -> Any:
        return await session.list_tools()

    listed = call(InMemoryIdempotencyStore(), go)
    assert sorted(t.name for t in listed.tools) == list(TOOL_NAMES)
    for tool in listed.tools:
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None


def test_a_call_with_a_context_returns_the_structured_result(telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore()
    result = _call(store, "create_bitbucket_account", BITBUCKET_ARGS)
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "active"
    [span] = [s for s in telemetry.spans() if s.name == SPAN_PREFIX + "create_bitbucket_account"]
    attrs = dict(span.attributes or {})
    assert (attrs["outcome"], attrs["org_id"], attrs["agent.name"], attrs["run_id"]) == (
        "ok",
        "org_a",
        "provisioning_agent",
        "run_1",
    )
    assert attrs["mock.replayed"] is False


def test_replaying_a_call_through_mcp_returns_the_same_result_and_creates_nothing_new(
    telemetry: CapturedTelemetry,
) -> None:
    store = InMemoryIdempotencyStore()
    first = _call(store, "provision_laptop", LAPTOP_ARGS)
    again = _call(store, "provision_laptop", LAPTOP_ARGS)
    assert again.structuredContent == first.structuredContent
    assert _text(again) == _text(first)
    assert store.count() == 1
    replayed = [dict(s.attributes or {})["mock.replayed"] for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert replayed == [False, True]


@pytest.mark.parametrize("header", ["", "not-a-jws"])
def test_a_call_without_a_valid_context_is_denied(header: str, telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore()
    result = _call(store, "create_bitbucket_account", BITBUCKET_ARGS, header=header)
    assert result.isError is True
    assert "only through the MCP hub" in _text(result) or "refused" in _text(result)
    assert store.count() == 0
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["denial.reason"] == "no_call_context"


def test_a_context_signed_for_mcp_mongo_is_denied() -> None:
    """The mongo audience never authorises a mock call, even with the right key and tool."""
    store = InMemoryIdempotencyStore()
    result = _call(store, "create_bitbucket_account", BITBUCKET_ARGS, audience="mcp-mongo")
    assert result.isError is True
    assert store.count() == 0


def test_a_context_signed_with_another_key_is_denied() -> None:
    store = InMemoryIdempotencyStore()
    forged = sign_call_context(CTX_A, "another-key-that-is-long-enough-0123456789", audience="mcp-mocks")
    result = _call(store, "create_bitbucket_account", BITBUCKET_ARGS, header=forged)
    assert result.isError is True
    assert store.count() == 0


def test_a_context_naming_another_tool_is_denied(telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore()
    for_laptop = sign_call_context(dataclasses.replace(CTX_A, tool="provision_laptop"), KEY, audience="mcp-mocks")
    result = _call(store, "create_bitbucket_account", BITBUCKET_ARGS, header=for_laptop)
    assert result.isError is True
    assert store.count() == 0
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["denial.reason"] == "context_tool_mismatch"


def test_invalid_arguments_are_refused_without_echoing_values(telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore()
    result = _call(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "workspace": "Secret Value", "org_id": "org_b"})
    assert result.isError is True
    assert "Secret Value" not in _text(result)
    assert "org_b" not in _text(result)
    assert store.count() == 0
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["denial.reason"] == "invalid_arguments"


def test_a_conflicting_replay_is_refused_as_a_conflict_not_an_internal_error(telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore()
    _call(store, "create_bitbucket_account", BITBUCKET_ARGS)
    result = _call(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "groups": ["admins"]})
    assert result.isError is True
    assert "idempotency_conflict" in _text(result)
    assert not _text(result).endswith("internal error")
    assert store.count() == 1


def test_a_full_store_is_an_internal_error_without_detail(telemetry: CapturedTelemetry) -> None:
    store = InMemoryIdempotencyStore(max_records=0)
    result = _call(store, "provision_laptop", LAPTOP_ARGS)
    assert result.isError is True
    assert _text(result) == "provision_laptop: internal error"
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["outcome"] == "error"


def test_the_server_needs_a_long_enough_key() -> None:
    with pytest.raises(ValueError, match="32"):
        build_server(InMemoryIdempotencyStore(), "short")
