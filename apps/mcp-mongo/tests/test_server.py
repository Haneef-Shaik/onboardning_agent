"""mcp-mongo over streamable HTTP with a real MCP client (PRD §7.5). The driver is faked; the
repositories, policy, contracts and server are real."""

from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId
from mcp import ClientSession
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import CallContext, sign_call_context

from .fakes import FakeDatabase
from .harness import CTX_A, KEY, call

TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}


def _tool(name: str, args: dict[str, Any]) -> Any:
    async def fn(session: ClientSession) -> CallToolResult:
        return await session.call_tool(name, args)

    return fn


def _error_text(result: CallToolResult) -> str:
    assert result.isError, result
    return " ".join(getattr(block, "text", "") for block in result.content)


def _seed(db: FakeDatabase) -> None:
    db["new_hires"].docs.extend(
        [
            {"_id": ObjectId(), "org_id": "org_a", "name": "ptk_a1", "status": "open"},
            {"_id": ObjectId(), "org_id": "org_a", "name": "ptk_a2", "status": "done"},
            {"_id": ObjectId(), "org_id": "org_b", "name": "ptk_b1", "status": "open"},
        ]
    )


# --- tools/list ---------------------------------------------------------------------------------


def test_the_server_lists_exactly_the_prd_tools_with_closed_schemas(db: FakeDatabase) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.list_tools()

    listed = call(db, fn)
    assert {tool.name for tool in listed.tools} == TOOLS
    for tool in listed.tools:
        assert tool.inputSchema["additionalProperties"] is False, tool.name
        assert tool.outputSchema is not None, tool.name
    find = next(tool for tool in listed.tools if tool.name == "find")
    assert "limit" in find.inputSchema["required"]
    assert find.inputSchema["properties"]["limit"]["maximum"] == 100


def test_no_destructive_or_admin_tool_is_served(db: FakeDatabase) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.list_tools()

    names = {tool.name for tool in call(db, fn).tools}
    for word in ("delete", "drop", "index", "admin", "command", "rename"):
        assert not any(word in name.lower() for name in names), word


# --- reads --------------------------------------------------------------------------------------


def test_find_returns_only_the_callers_org(db: FakeDatabase) -> None:
    _seed(db)
    result = call(db, _tool("find", {"collection": "new_hires", "filter": {"status": "open"}, "limit": 10}))
    assert not result.isError
    assert result.structuredContent is not None
    docs = result.structuredContent["documents"]
    assert [d["name"] for d in docs] == ["ptk_a1"]
    assert result.structuredContent["count"] == 1
    sent = db["new_hires"].calls[-1][1]
    assert sent["filter"] == {"status": "open", "org_id": "org_a"}
    assert sent["limit"] == 10


def test_find_serialises_object_ids_and_accepts_them_back(db: FakeDatabase) -> None:
    _seed(db)
    oid = db["new_hires"].docs[0]["_id"]
    result = call(db, _tool("find", {"collection": "new_hires", "filter": {"_id": {"$oid": str(oid)}}, "limit": 1}))
    assert result.structuredContent is not None
    assert result.structuredContent["documents"][0]["_id"] == str(oid)
    assert db["new_hires"].calls[-1][1]["filter"]["_id"] == oid


@pytest.mark.parametrize("args", [{}, {"limit": 101}, {"limit": 0}, {"limit": 1000}])
def test_find_without_limit_or_with_limit_over_100_is_rejected(db: FakeDatabase, args: dict[str, Any]) -> None:
    result = call(db, _tool("find", {"collection": "new_hires", **args}))
    assert "limit" in _error_text(result)
    assert db.calls() == []


def test_count_is_scoped_to_the_callers_org(db: FakeDatabase) -> None:
    _seed(db)
    result = call(db, _tool("count", {"collection": "new_hires", "filter": {"status": "open"}}))
    assert result.structuredContent == {"count": 1}
    assert db["new_hires"].calls[-1] == ("count_documents", {"status": "open", "org_id": "org_a"})


def test_aggregate_matches_the_org_first_and_caps_the_result(db: FakeDatabase) -> None:
    _seed(db)
    pipeline = [{"$sort": {"name": 1}}, {"$project": {"name": 1}}]
    result = call(db, _tool("aggregate", {"collection": "new_hires", "pipeline": pipeline}))
    assert not result.isError
    sent = db["new_hires"].calls[-1][1]
    assert sent[0] == {"$match": {"org_id": "org_a"}}
    assert sent[1:-1] == pipeline
    assert sent[-1] == {"$limit": 100}


def test_aggregate_rejects_a_stage_off_the_allowlist(db: FakeDatabase) -> None:
    pipeline = [{"$lookup": {"from": "pii_vault", "localField": "a", "foreignField": "b", "as": "c"}}]
    result = call(db, _tool("aggregate", {"collection": "new_hires", "pipeline": pipeline}))
    assert "stage" in _error_text(result)
    assert db.calls() == []


def test_list_collections_returns_only_the_agents_collections(db: FakeDatabase) -> None:
    result = call(db, _tool("list_collections", {}))
    assert result.structuredContent == {"collections": ["new_hires", "run_tasks"]}


def test_reading_a_collection_outside_the_context_is_rejected(db: FakeDatabase) -> None:
    result = call(db, _tool("find", {"collection": "pii_vault", "limit": 1}))
    assert "not readable" in _error_text(result)
    assert db.calls() == []


# --- org_id -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("find", {"collection": "new_hires", "filter": {"org_id": "org_b"}, "limit": 10}),
        ("find", {"collection": "new_hires", "filter": {"org_id": "org_a"}, "limit": 10}),
        ("find", {"collection": "new_hires", "filter": {"$or": [{"org_id": "org_b"}]}, "limit": 10}),
        ("count", {"collection": "new_hires", "filter": {"org_id": {"$ne": "org_a"}}}),
        ("aggregate", {"collection": "new_hires", "pipeline": [{"$match": {"org_id": "org_b"}}]}),
        ("update_one", {"collection": "run_tasks", "filter": {"org_id": "org_b"}, "update": {"$set": {"a": 1}}}),
        ("update_one", {"collection": "run_tasks", "filter": {"a": 1}, "update": {"$set": {"org_id": "org_b"}}}),
        ("insert_one", {"collection": "run_tasks", "document": {"org_id": "org_b", "title": "x"}}),
    ],
)
def test_an_agent_supplied_org_id_is_rejected(db: FakeDatabase, tool: str, args: dict[str, Any]) -> None:
    result = call(db, _tool(tool, args))
    assert "org_id" in _error_text(result)
    assert db.calls() == []


@pytest.mark.parametrize("tool", ["find", "count", "insert_one", "list_collections"])
def test_org_id_as_a_tool_argument_is_rejected(db: FakeDatabase, tool: str) -> None:
    args: dict[str, Any] = {"collection": "run_tasks", "org_id": "org_b", "limit": 1, "document": {}}
    if tool == "count":
        args = {"collection": "run_tasks", "org_id": "org_b"}
    if tool == "insert_one":
        args = {"collection": "run_tasks", "org_id": "org_b", "document": {}}
    if tool == "list_collections":
        args = {"org_id": "org_b"}
    _error_text(call(db, _tool(tool, args)))
    assert db.calls() == []


# --- writes -------------------------------------------------------------------------------------


def test_insert_gets_org_id_from_the_context(db: FakeDatabase) -> None:
    result = call(db, _tool("insert_one", {"collection": "run_tasks", "document": {"title": "Laptop"}}))
    assert not result.isError
    assert result.structuredContent is not None
    stored = db["run_tasks"].docs[0]
    assert stored["org_id"] == "org_a"
    assert stored["title"] == "Laptop"
    assert result.structuredContent == {"inserted_id": str(stored["_id"]), "count": 1}


def test_insert_into_a_read_only_collection_is_rejected(db: FakeDatabase) -> None:
    result = call(db, _tool("insert_one", {"collection": "new_hires", "document": {"title": "x"}}))
    assert "not writable" in _error_text(result)
    assert db.calls() == []


def test_update_is_scoped_to_the_callers_org(db: FakeDatabase) -> None:
    db["run_tasks"].docs.append({"_id": 1, "org_id": "org_a", "state": "todo"})
    args = {"collection": "run_tasks", "filter": {"_id": 1}, "update": {"$set": {"state": "done"}}}
    result = call(db, _tool("update_one", args))
    assert result.structuredContent == {"matched": 1, "modified": 1, "count": 1}
    assert db["run_tasks"].calls[-1][1] == {
        "filter": {"_id": 1, "org_id": "org_a"},
        "update": {"$set": {"state": "done"}},
    }


def test_an_update_without_operators_is_rejected(db: FakeDatabase) -> None:
    args = {"collection": "run_tasks", "filter": {"_id": 1}, "update": {"state": "done"}}
    result = call(db, _tool("update_one", args))
    assert "update operators" in _error_text(result)
    assert db.calls() == []


# --- the call context ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "",
        "not-a-jws",
        sign_call_context(CTX_A, "another-key-that-is-long-enough-0123456789"),
    ],
)
def test_a_call_without_a_valid_hub_context_is_refused(db: FakeDatabase, header: str) -> None:
    _seed(db)
    result = call(db, _tool("find", {"collection": "new_hires", "limit": 10}), header=header)
    assert "call context" in _error_text(result)
    assert db.calls() == []


def test_the_org_comes_from_the_context_of_each_call(db: FakeDatabase) -> None:
    _seed(db)
    ctx_b = CallContext(
        org_id="org_b", agent="db_agent", run_id="run_2", read=frozenset({"new_hires"}), write=frozenset()
    )
    result = call(db, _tool("count", {"collection": "new_hires"}), header=sign_call_context(ctx_b, KEY))
    assert result.structuredContent == {"count": 1}
    assert db["new_hires"].calls[-1] == ("count_documents", {"org_id": "org_b"})


def test_an_unexpected_failure_hides_its_message(db: FakeDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("driver said ptk_secret_detail")

    monkeypatch.setattr(db["new_hires"], "count_documents", boom)
    text = _error_text(call(db, _tool("count", {"collection": "new_hires"})))
    assert "ptk_secret_detail" not in text
    assert "internal error" in text


def test_an_invalid_argument_error_names_the_field_but_not_the_value(db: FakeDatabase) -> None:
    args = {"collection": "new_hires", "limit": "ptk_secret_limit", "org_id": "ptk_secret_org"}
    text = _error_text(call(db, _tool("find", args)))
    assert "limit" in text
    assert "org_id" in text
    assert "ptk_secret" not in text
