"""An MCP client lists and calls every mcp-mongo tool over streamable HTTP against a testcontainers
MongoDB (MCP-1 box 1), plus the limit, org_id and span rules end to end (boxes 2-4)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any

import httpx
import pytest
from bson import ObjectId
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import HEADER, CallContext, sign_call_context
from pymongo.database import Database

from .conftest import McpServer

# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root read-back across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]
TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}
CONTENT = "Zq7-int-document-content"


def _ctx(org_id: str, tool: str) -> CallContext:
    return CallContext(
        org_id=org_id,
        agent="db_agent",
        run_id=f"run_{org_id}",
        tool=tool,
        read=frozenset({"new_hires"}),
        write=frozenset({"run_tasks"}),
    )


def _session[T](server: McpServer, org_id: str, fn: Callable[[ClientSession], Awaitable[T]]) -> T:
    """Like the hub, sign a fresh context for each `tools/call`, naming that tool."""

    async def sign(request: httpx.Request) -> None:
        body = json.loads(request.content or b"{}")
        if isinstance(body, dict) and body.get("method") == "tools/call":
            request.headers[HEADER] = sign_call_context(_ctx(org_id, body["params"]["name"]), server.key)

    async def main() -> T:
        async with (
            httpx.AsyncClient(event_hooks={"request": [sign]}, timeout=30) as http,
            streamable_http_client(server.url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await fn(session)

    return asyncio.run(main())


def _call(server: McpServer, org_id: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    async def fn(session: ClientSession) -> CallToolResult:
        return await session.call_tool(tool, args)

    return _session(server, org_id, fn)


def _data(result: CallToolResult) -> dict[str, Any]:
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def _error(result: CallToolResult) -> str:
    assert result.isError, result
    return " ".join(getattr(block, "text", "") for block in result.content)


@pytest.fixture
def seeded(root_db: Database[dict[str, Any]], unscoped: Unscoped) -> Database[dict[str, Any]]:
    with unscoped("test setup: clear both orgs' seed documents as root"):
        for name in ("new_hires", "run_tasks"):
            root_db[name].delete_many({})
    root_db["new_hires"].insert_many(
        [
            {"org_id": "org_a", "name": "ptk_a1", "status": "open", "note": CONTENT},
            {"org_id": "org_a", "name": "ptk_a2", "status": "done", "note": CONTENT},
            {"org_id": "org_a", "name": "ptk_a3", "status": "open", "note": CONTENT},
            {"org_id": "org_b", "name": "ptk_b1", "status": "open", "note": CONTENT},
        ]
    )
    root_db["run_tasks"].insert_many(
        [
            {"_id": "task_1", "org_id": "org_a", "state": "todo"},
            {"_id": "task_2", "org_id": "org_b", "state": "todo"},
        ]
    )
    return root_db


# --- box 1: list and call every tool ------------------------------------------------------------


def test_an_mcp_client_lists_every_tool_with_its_contract(mcp_server: McpServer) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.list_tools()

    listed = _session(mcp_server, "org_a", fn)
    assert {tool.name for tool in listed.tools} == TOOLS
    for tool in listed.tools:
        assert tool.inputSchema["type"] == "object"
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None


def test_list_collections(mcp_server: McpServer, seeded: Any) -> None:
    assert _data(_call(mcp_server, "org_a", "list_collections", {})) == {"collections": ["new_hires", "run_tasks"]}


def test_find_sees_only_the_callers_org(mcp_server: McpServer, seeded: Any) -> None:
    args = {"collection": "new_hires", "filter": {"status": "open"}, "sort": [["name", 1]], "limit": 10}
    data = _data(_call(mcp_server, "org_a", "find", args))
    assert [d["name"] for d in data["documents"]] == ["ptk_a1", "ptk_a3"]
    assert all(d["org_id"] == "org_a" for d in data["documents"])
    assert data["count"] == 2
    other = _data(_call(mcp_server, "org_b", "find", args))
    assert [d["name"] for d in other["documents"]] == ["ptk_b1"]


def test_find_by_object_id_round_trips(
    mcp_server: McpServer, seeded: Database[dict[str, Any]], unscoped: Unscoped
) -> None:
    with unscoped("root lookup of a seeded document's _id, to hand the tool a real ObjectId"):
        doc = seeded["new_hires"].find_one({"name": "ptk_a2"})
    assert doc is not None
    args = {"collection": "new_hires", "filter": {"_id": {"$oid": str(doc["_id"])}}, "limit": 1}
    data = _data(_call(mcp_server, "org_a", "find", args))
    assert [d["_id"] for d in data["documents"]] == [str(doc["_id"])]
    with unscoped("root lookup of the other org's document _id, to try it from org_a"):
        foreign = seeded["new_hires"].find_one({"name": "ptk_b1"})
    assert foreign is not None
    args = {"collection": "new_hires", "filter": {"_id": {"$oid": str(foreign["_id"])}}, "limit": 1}
    assert _data(_call(mcp_server, "org_a", "find", args))["documents"] == []


def test_count(mcp_server: McpServer, seeded: Any) -> None:
    assert _data(_call(mcp_server, "org_a", "count", {"collection": "new_hires", "filter": {"status": "open"}})) == {
        "count": 2
    }
    assert _data(_call(mcp_server, "org_b", "count", {"collection": "new_hires"})) == {"count": 1}


def test_aggregate_groups_within_the_callers_org(mcp_server: McpServer, seeded: Any) -> None:
    pipeline = [{"$group": {"_id": "$status", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    data = _data(_call(mcp_server, "org_a", "aggregate", {"collection": "new_hires", "pipeline": pipeline}))
    assert data["documents"] == [{"_id": "done", "n": 1}, {"_id": "open", "n": 2}]


def test_insert_one_takes_org_id_from_the_context(
    mcp_server: McpServer, seeded: Database[dict[str, Any]], unscoped: Unscoped
) -> None:
    data = _data(_call(mcp_server, "org_a", "insert_one", {"collection": "run_tasks", "document": {"state": "new"}}))
    with unscoped("root read-back by id, without assuming the org, proves which org the tool stamped"):
        stored = seeded["run_tasks"].find_one({"_id": ObjectId(data["inserted_id"])})
    assert stored is not None
    assert stored["org_id"] == "org_a"
    assert stored["state"] == "new"


def test_update_one_cannot_reach_another_org(
    mcp_server: McpServer, seeded: Database[dict[str, Any]], unscoped: Unscoped
) -> None:
    args = {"collection": "run_tasks", "filter": {"_id": "task_2"}, "update": {"$set": {"state": "done"}}}
    assert _data(_call(mcp_server, "org_a", "update_one", args)) == {"matched": 0, "modified": 0, "count": 0}
    with unscoped("root read-back by id proves the other org's task is unchanged"):
        task_2 = seeded["run_tasks"].find_one({"_id": "task_2"})
    assert task_2 == {"_id": "task_2", "org_id": "org_b", "state": "todo"}
    args["filter"] = {"_id": "task_1"}
    assert _data(_call(mcp_server, "org_a", "update_one", args)) == {"matched": 1, "modified": 1, "count": 1}
    with unscoped("root read-back by id proves the own org's task was updated"):
        task_1 = seeded["run_tasks"].find_one({"_id": "task_1"})
    assert task_1 == {"_id": "task_1", "org_id": "org_a", "state": "done"}


def test_an_id_from_find_updates_the_document_as_oid(
    mcp_server: McpServer, seeded: Database[dict[str, Any]], unscoped: Unscoped
) -> None:
    created = _data(_call(mcp_server, "org_a", "insert_one", {"collection": "run_tasks", "document": {"state": "a"}}))
    found = _data(_call(mcp_server, "org_a", "find", {"collection": "run_tasks", "filter": {"state": "a"}, "limit": 1}))
    assert found["documents"][0]["_id"] == created["inserted_id"]
    args = {
        "collection": "run_tasks",
        "filter": {"_id": {"$oid": found["documents"][0]["_id"]}},
        "update": {"$set": {"state": "b"}},
    }
    assert _data(_call(mcp_server, "org_a", "update_one", args)) == {"matched": 1, "modified": 1, "count": 1}
    with unscoped("root read-back by id proves the $oid update landed"):
        stored = seeded["run_tasks"].find_one({"_id": ObjectId(created["inserted_id"])})
    assert stored is not None
    assert stored["state"] == "b"


def test_a_query_mongodb_refuses_is_a_denial_not_an_internal_error(mcp_server: McpServer, seeded: Any) -> None:
    mcp_server.telemetry.clear()
    # Passes the MCP-2 validator (allowlisted `$mod`), but MongoDB refuses a zero divisor.
    args = {"collection": "new_hires", "filter": {"status": {"$mod": [0, 0]}}, "limit": 1}
    text = _error(_call(mcp_server, "org_a", "find", args))
    assert "rejected" in text
    assert "internal error" not in text
    (span,) = [s for s in mcp_server.telemetry.spans() if s.name == "mcp.mongo.find"]
    assert dict(span.attributes or {})["denial.reason"] == "query_rejected"


# --- box 2: find limit--------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"limit": 101}], ids=["no-limit", "limit-101"])
def test_find_without_limit_or_over_100_is_rejected(mcp_server: McpServer, seeded: Any, extra: dict[str, Any]) -> None:
    assert "limit" in _error(_call(mcp_server, "org_a", "find", {"collection": "new_hires", **extra}))


# --- box 3: org_id ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("find", {"collection": "new_hires", "filter": {"org_id": "org_b"}, "limit": 10}),
        ("count", {"collection": "new_hires", "filter": {"$or": [{"org_id": "org_b"}]}}),
        ("aggregate", {"collection": "new_hires", "pipeline": [{"$match": {"org_id": "org_b"}}]}),
        ("insert_one", {"collection": "run_tasks", "document": {"org_id": "org_b"}}),
        (
            "update_one",
            {"collection": "run_tasks", "filter": {"_id": "task_1"}, "update": {"$set": {"org_id": "org_b"}}},
        ),
    ],
)
def test_an_agent_supplied_org_id_is_rejected(
    mcp_server: McpServer, seeded: Database[dict[str, Any]], tool: str, args: dict[str, Any], unscoped: Unscoped
) -> None:
    with unscoped("root snapshot of every org's tasks before the attempt"):
        before = sorted((d["_id"], d["org_id"]) for d in seeded["run_tasks"].find())
    assert "org_id" in _error(_call(mcp_server, "org_a", tool, args))
    with unscoped("root snapshot of every org's tasks proves nothing moved"):
        after = sorted((d["_id"], d["org_id"]) for d in seeded["run_tasks"].find())
    assert after == before


# --- box 4: spans, through the real process -----------------------------------------------------


def test_every_call_span_has_the_attributes_and_no_document_content(mcp_server: McpServer, seeded: Any) -> None:
    mcp_server.telemetry.clear()
    _call(mcp_server, "org_a", "find", {"collection": "new_hires", "filter": {"status": "open"}, "limit": 10})
    _call(mcp_server, "org_a", "insert_one", {"collection": "run_tasks", "document": {"note": CONTENT}})
    spans = mcp_server.telemetry.spans()
    tool_spans = [s for s in spans if s.name.startswith("mcp.mongo.")]
    assert [s.name for s in tool_spans] == ["mcp.mongo.find", "mcp.mongo.insert_one"]
    for span in tool_spans:
        attrs = dict(span.attributes or {})
        assert {"db.operation", "db.collection", "agent.name", "org_id", "db.result_count"} <= attrs.keys()
        assert attrs["org_id"] == "org_a"
    assert dict(tool_spans[0].attributes or {})["db.result_count"] == 2
    pymongo_spans = [s for s in spans if s.instrumentation_scope and "pymongo" in s.instrumentation_scope.name]
    assert pymongo_spans, "the driver is instrumented"
    for span in spans:
        text = repr(dict(span.attributes or {})) + repr([dict(e.attributes or {}) for e in span.events])
        assert CONTENT not in text, span.name
        assert "ptk_a1" not in text, span.name
