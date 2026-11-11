"""Fixes from the MOCK-1 code and security reviews, each pinned by a test."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from onb_mcp_mocks import server as server_module
from onb_mcp_mocks import tools
from onb_mcp_mocks.contracts import CONTRACTS
from onb_mcp_mocks.server import SPAN_PREFIX
from onb_mcp_mocks.store import InMemoryIdempotencyStore, Outcome, StoreFullError
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_telemetry.testing import CapturedTelemetry

from .harness import BITBUCKET_ARGS, CTX_A, LAPTOP_ARGS, call, run


def _text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def _call(store: InMemoryIdempotencyStore, name: str, args: dict[str, Any]) -> CallToolResult:
    async def go(session: ClientSession) -> CallToolResult:
        return await session.call_tool(name, args)

    return call(store, go)


def _invoke(store: InMemoryIdempotencyStore, name: str, args: dict[str, Any], org: str = "org_a") -> dict[str, Any]:
    ctx = dataclasses.replace(CTX_A, org_id=org, tool=name)
    result = run(tools.invoke(store, ctx, name, CONTRACTS[name].args.model_validate(args)))
    return result.model_dump(mode="json")


def test_a_broken_result_is_an_internal_error_not_the_agents_invalid_arguments(
    monkeypatch: pytest.MonkeyPatch, telemetry: CapturedTelemetry
) -> None:
    """A server-side bug must read as `<tool>: internal error` (the hub's `downstream_error`)."""

    async def broken(*_: Any) -> Outcome:
        return Outcome({"order_id": "x", "eta_days": "soon"}, replayed=False)

    monkeypatch.setattr(server_module, "invoke_outcome", broken)
    result = _call(InMemoryIdempotencyStore(), "provision_laptop", LAPTOP_ARGS)
    assert _text(result) == "provision_laptop: internal error"
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["outcome"] == "error"


def test_ids_differ_when_the_same_key_comes_back_with_other_arguments_after_a_restart() -> None:
    """The conflict check lives in memory; the ids include the arguments, so one id never names two hires."""
    other_hire = {**BITBUCKET_ARGS, "hire_id": "65f0aaaabbbbccccddddffff"}
    first = _invoke(InMemoryIdempotencyStore(), "create_bitbucket_account", BITBUCKET_ARGS)
    after_restart = _invoke(InMemoryIdempotencyStore(), "create_bitbucket_account", other_hire)
    assert first["account_id"] != after_restart["account_id"]


def test_group_order_does_not_make_a_replay_a_conflict() -> None:
    store = InMemoryIdempotencyStore()
    first = _invoke(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "groups": ["backend", "admins"]})
    again = _invoke(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "groups": ["admins", "backend"]})
    assert again == first
    assert first["groups"] == ["admins", "backend"]
    assert store.count() == 1


def test_one_org_filling_its_quota_does_not_block_another_org() -> None:
    store = InMemoryIdempotencyStore(max_records_per_org=1)
    _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS, org="org_a")
    with pytest.raises(StoreFullError):
        _invoke(store, "provision_laptop", LAPTOP_ARGS, org="org_a")
    assert _invoke(store, "provision_laptop", LAPTOP_ARGS, org="org_b")["status"] == "ordered"


def test_a_full_quota_is_a_refusal_named_store_full(telemetry: CapturedTelemetry) -> None:
    result = _call(InMemoryIdempotencyStore(max_records_per_org=0), "provision_laptop", LAPTOP_ARGS)
    assert result.isError is True
    assert _text(result).startswith("store_full:")
    [span] = [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]
    assert dict(span.attributes or {})["denial.reason"] == "store_full"


def test_concurrent_first_calls_with_one_key_create_one_record() -> None:
    store = InMemoryIdempotencyStore()
    name = "create_bitbucket_account"
    args = CONTRACTS[name].args.model_validate(BITBUCKET_ARGS)
    ctx = dataclasses.replace(CTX_A, tool=name)

    async def race() -> list[Any]:
        return list(await asyncio.gather(*(tools.invoke(store, ctx, name, args) for _ in range(20))))

    results = run(race())
    assert len({r.account_id for r in results}) == 1
    assert (store.count(), store.creations) == (1, 1)


def test_the_store_works_across_event_loops() -> None:
    store = InMemoryIdempotencyStore()
    first = _invoke(store, "provision_laptop", LAPTOP_ARGS)  # one asyncio.run
    assert _invoke(store, "provision_laptop", LAPTOP_ARGS) == first  # another


def test_a_group_chat_id_is_accepted_like_the_bot_api() -> None:
    assert run(TelegramMockTransport().send_message(-1001234567890, "hello team")).message_id == 1
