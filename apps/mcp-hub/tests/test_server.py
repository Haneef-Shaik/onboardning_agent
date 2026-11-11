"""The hub over MCP (streamable HTTP), end to end in process: agent client → hub → real mcp-mongo."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from onb_agents.tokens import hub_client_config, issue_agent_token
from onb_data import AuditOutcome, tenant_scope
from onb_data.audit import AuditQuery
from onb_data.testing import InMemoryAuditLog
from onb_mcp_hub.catalog import mongo_catalog
from onb_mcp_hub.downstream import McpDownstream
from onb_mcp_hub.gateway import Gateway
from onb_mcp_mongo.call_context import HEADER
from onb_mcp_mongo.server import build_server
from onb_telemetry.testing import CapturedTelemetry

from .agents import claims, registered
from .fakes import CONTEXT_KEY, TOKEN_KEY, FakeDownstream, FakeRegistry, ok
from .harness import FakeDatabase, hub_session, make_store

FIND = {"collection": "run_tasks", "filter": {"status": "open"}, "limit": 10}
HUB_URL = "http://mcp-hub/mcp"


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def agent_headers(**kw: Any) -> dict[str, str]:
    config = hub_client_config(HUB_URL, issue_agent_token(claims(**kw), TOKEN_KEY))
    headers: dict[str, str] = config["headers"]
    return headers


def run_with_fake(
    fn: Callable[[ClientSession], Awaitable[Any]], downstream: FakeDownstream, headers: dict[str, str]
) -> Any:
    gateway = Gateway(
        catalog=mongo_catalog(),
        registry=FakeRegistry({"db_agent": registered("db_agent")}),
        auditor=InMemoryAuditLog(),
        downstream=downstream,
        token_key=TOKEN_KEY,
    )

    async def go() -> Any:
        async with hub_session(gateway, headers) as session:
            return await fn(session)

    return asyncio.run(go())


def test_the_hub_lists_every_catalog_tool_with_its_contract() -> None:
    listed = run_with_fake(lambda s: s.list_tools(), FakeDownstream(), agent_headers())
    tools = {t.name: t for t in listed.tools}
    assert sorted(tools) == sorted(mongo_catalog())
    assert tools["mongo__find"].inputSchema["required"] == ["collection", "limit"]
    assert tools["mongo__find"].outputSchema is not None
    assert CONTEXT_KEY not in listed.model_dump_json()


def test_a_call_over_mcp_carries_the_agent_token_to_the_gateway() -> None:
    downstream = FakeDownstream(reply=lambda sent: ok({"documents": [{"x": 1}], "count": 1}))
    result = run_with_fake(lambda s: s.call_tool("mongo__find", FIND), downstream, agent_headers())
    assert result.isError is False
    assert result.structuredContent == {"documents": [{"x": 1}], "count": 1}
    assert downstream.sent[0].context.org_id == "org_acme"


def test_a_call_without_a_token_is_denied_over_mcp() -> None:
    result = run_with_fake(lambda s: s.call_tool("mongo__find", FIND), FakeDownstream(), {})
    assert result.isError is True
    assert "denied (no_token)" in text(result)


def test_an_unknown_tool_over_mcp_is_denied_by_the_gateway() -> None:
    result = run_with_fake(lambda s: s.call_tool("mongo__drop", {}), FakeDownstream(), agent_headers())
    assert "denied (unknown_tool)" in text(result)


def test_a_request_for_another_host_is_refused() -> None:
    from onb_mcp_hub.server import build_hub

    hub = build_hub(
        Gateway(
            catalog=mongo_catalog(),
            registry=FakeRegistry(),
            auditor=InMemoryAuditLog(),
            downstream=FakeDownstream(),
            token_key=TOKEN_KEY,
        ),
        allowed_hosts=("mcp-hub",),
    )

    async def go() -> int:
        async with hub.manager.run():
            transport = httpx.ASGITransport(app=hub.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://evil.example") as http:
                response = await http.post("/mcp", json={}, headers={"accept": "application/json, text/event-stream"})
                return response.status_code

    assert asyncio.run(go()) in (403, 421)


# --- end to end through the real mcp-mongo (fake database under its DataStore) ----------------------


class Stack:
    def __init__(self, database: FakeDatabase, agents: dict[str, Any] | None = None) -> None:
        self.mongo = build_server(make_store(database), CONTEXT_KEY)
        mongo_app = self.mongo.streamable_http_app()
        self.forwarded: list[httpx.Request] = []

        def client(headers: dict[str, str]) -> httpx.AsyncClient:
            async def record(request: httpx.Request) -> None:
                self.forwarded.append(request)

            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mongo_app),
                base_url="http://mcp-mongo",
                headers=headers,
                event_hooks={"request": [record]},
            )

        self.audit = InMemoryAuditLog()
        self.gateway = Gateway(
            catalog=mongo_catalog(),
            registry=FakeRegistry(agents or {"db_agent": registered("db_agent")}),
            auditor=self.audit,
            downstream=McpDownstream({"mongo": "http://mcp-mongo/mcp"}, CONTEXT_KEY, client=client),
            token_key=TOKEN_KEY,
        )

    def run(self, fn: Callable[[ClientSession], Awaitable[Any]], headers: dict[str, str]) -> Any:
        async def go() -> Any:
            async with self.mongo.session_manager.run(), hub_session(self.gateway, headers) as session:
                return await fn(session)

        return asyncio.run(go())

    def records(self, org: str) -> list[Any]:
        async def page() -> list[Any]:
            with tenant_scope(org):
                return list((await self.audit.list(AuditQuery(), page=1, limit=100)).entries)

        return asyncio.run(page())


def seeded() -> FakeDatabase:
    database = FakeDatabase()
    database["run_tasks"].docs.extend(
        [
            {"_id": "a1", "org_id": "org_acme", "status": "open", "note": "call 9876543210 about laptop"},
            {"_id": "g1", "org_id": "org_globex", "status": "open", "note": "globex only"},
        ]
    )
    return database


def test_end_to_end_the_driver_sees_only_the_token_org(telemetry: CapturedTelemetry) -> None:
    database = seeded()
    stack = Stack(database)
    args = {"collection": "run_tasks", "filter": {"status": "open", "owner": "org_globex"}, "limit": 10}
    stack.run(lambda s: s.call_tool("mongo__find", args), agent_headers(org="org_acme"))
    [(_, query)] = database["run_tasks"].calls
    assert query["org_id"] == "org_acme"


def test_end_to_end_a_raw_mobile_from_mcp_mongo_is_redacted(telemetry: CapturedTelemetry) -> None:
    stack = Stack(seeded())
    result = stack.run(lambda s: s.call_tool("mongo__find", FIND), agent_headers())
    assert result.isError is False
    body = text(result) + json.dumps(result.structuredContent)
    assert "9876543210" not in body
    assert "[MOBILE]" in body
    assert "globex" not in body


def test_end_to_end_credentials_are_injected_by_the_hub_and_never_returned(telemetry: CapturedTelemetry) -> None:
    stack = Stack(seeded())
    headers = agent_headers()
    result = stack.run(lambda s: s.call_tool("mongo__find", FIND), headers)
    tool_posts = [r for r in stack.forwarded if b'"tools/call"' in r.content]
    assert tool_posts, "the hub forwarded the call"
    context = tool_posts[0].headers[HEADER]
    assert "authorization" not in {k.lower() for k in tool_posts[0].headers}  # the agent token stops at the hub
    returned = text(result) + json.dumps(result.structuredContent)
    for secret in (CONTEXT_KEY, context, TOKEN_KEY):
        assert secret not in returned
    agent_side = json.dumps(headers)
    for secret in (CONTEXT_KEY, context):
        assert secret not in agent_side


def test_end_to_end_a_denial_from_mcp_mongo_is_audited(telemetry: CapturedTelemetry) -> None:
    stack = Stack(seeded())
    args = {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"pii.mobile": "ptk_x"}}}
    result = stack.run(lambda s: s.call_tool("mongo__update_one", args), agent_headers())
    assert result.isError is True
    [record] = stack.records("org_acme")
    assert record.outcome is AuditOutcome.DENIED
    assert record.reason == "pii_read_only"  # caught at the hub by mcp-mongo's own validator


@pytest.mark.parametrize("tool", sorted(mongo_catalog()))
def test_every_hub_tool_reaches_mcp_mongo_with_a_context_for_that_tool(tool: str, telemetry: CapturedTelemetry) -> None:
    from .agents import xt_agent

    table: dict[str, dict[str, Any]] = {
        "mongo__list_collections": {},
        "mongo__find": FIND,
        "mongo__count": {"collection": "run_tasks", "filter": {}},
        "mongo__aggregate": {"collection": "run_tasks", "pipeline": [{"$match": {"status": "open"}}]},
        "mongo__insert_one": {"collection": "run_tasks", "document": {"status": "new"}},
        "mongo__update_one": {"collection": "run_tasks", "filter": {"status": "open"}, "update": {"$set": {"s": 1}}},
    }
    args = table[tool]
    stack = Stack(seeded(), {"xt_agent": xt_agent()})
    result = stack.run(lambda s: s.call_tool(tool, args), agent_headers(agent="xt_agent"))
    assert result.isError is False, text(result)
    assert [r.outcome for r in stack.records("org_acme")] == [AuditOutcome.SUCCESS]
