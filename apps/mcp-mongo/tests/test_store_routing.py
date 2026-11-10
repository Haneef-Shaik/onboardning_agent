"""Writes go through the read-write MongoDB user, everything else through the read-only one (MCP-3).

Two fake databases stand in for the two connections, so each test sees which one a tool reached.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from onb_mcp_mongo.server import WRITE_TOOLS, build_server

from .fakes import FakeDatabase, make_store
from .harness import CTX_A, KEY, run, signing_hook

CALLS: dict[str, dict[str, Any]] = {
    "list_collections": {},
    "find": {"collection": "new_hires", "filter": {}, "limit": 5},
    "count": {"collection": "new_hires", "filter": {}},
    "aggregate": {"collection": "new_hires", "pipeline": [{"$match": {"state": "x"}}]},
    "insert_one": {"collection": "run_tasks", "document": {"title": "Laptop"}},
    "update_one": {"collection": "run_tasks", "filter": {"title": "Laptop"}, "update": {"$set": {"done": True}}},
}


async def _call(read: FakeDatabase, write: FakeDatabase | None, tool: str) -> CallToolResult:
    write_store = make_store(write) if write is not None else None
    server = build_server(make_store(read), KEY, write_store=write_store)
    app = server.streamable_http_app()
    hooks: dict[str, Any] = {"event_hooks": {"request": [signing_hook(CTX_A, KEY)]}}
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp-mongo", **hooks) as http,
        streamable_http_client("http://mcp-mongo/mcp", http_client=http) as (rd, wr, _),
        ClientSession(rd, wr) as session,
    ):
        await session.initialize()
        return await session.call_tool(tool, CALLS[tool])


def test_the_write_tools_are_insert_one_and_update_one() -> None:
    assert frozenset({"insert_one", "update_one"}) == WRITE_TOOLS


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_each_tool_reaches_only_its_own_connection(tool: str) -> None:
    read, write = FakeDatabase(), FakeDatabase()
    result = run(_call(read, write, tool))
    assert not result.isError, result.content
    used, unused = (write, read) if tool in WRITE_TOOLS else (read, write)
    assert unused.calls() == []
    if tool != "list_collections":  # answered from the call context, no database call
        assert used.calls(), f"{tool} made no database call"


def test_without_a_write_store_the_writes_use_the_one_store() -> None:
    database = FakeDatabase()
    assert not run(_call(database, None, "insert_one")).isError
    assert database["run_tasks"].docs
