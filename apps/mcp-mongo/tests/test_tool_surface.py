"""No destructive or admin tool exists (MCP-2, PRD §7.5: "No delete, drop, index, admin or
`runCommand` tools"). Checked on the served tool list, on the contracts and handlers the server is
built from, and on the source, so a new tool or a driver call for one cannot slip in."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp import ClientSession
from onb_mcp_mongo.contracts import CONTRACTS
from onb_mcp_mongo.server import HANDLERS

from .fakes import FakeDatabase
from .harness import call

PRD_TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}
FORBIDDEN_WORDS = ("delete", "remove", "drop", "index", "admin", "command", "runcommand", "eval", "rename", "replace")
SRC = Path(__file__).resolve().parents[1] / "src" / "onb_mcp_mongo"
# Driver and database methods that delete, drop, index or run arbitrary commands.
FORBIDDEN_CALLS = re.compile(
    r"\.(delete_one|delete_many|drop|drop_collection|drop_database|drop_index|drop_indexes|create_index|"
    r"create_indexes|command|cursor_command|find_one_and_delete|find_one_and_replace|replace_one|bulk_write|"
    r"rename|create_collection)\s*\("
)


def _listed(db: FakeDatabase) -> list[Any]:
    async def fn(session: ClientSession) -> Any:
        return await session.list_tools()

    return list(call(db, fn).tools)


def test_no_delete_drop_index_admin_or_run_command_tool_is_served(db: FakeDatabase) -> None:
    tools = _listed(db)
    names = {tool.name for tool in tools}
    assert names == PRD_TOOLS
    for name in names:
        squashed = name.lower().replace("_", "")
        for word in FORBIDDEN_WORDS:
            assert word not in squashed, (name, word)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is False, tool.name


def test_the_server_is_built_only_from_the_prd_tools() -> None:
    assert set(CONTRACTS) == PRD_TOOLS
    assert set(HANDLERS) == PRD_TOOLS


def test_no_source_file_calls_a_delete_drop_index_or_command_method() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert files
    hits = [f"{path.name}: {m.group(0)}" for path in files for m in FORBIDDEN_CALLS.finditer(path.read_text())]
    assert hits == []


def test_calling_a_tool_that_does_not_exist_is_an_error(db: FakeDatabase) -> None:
    for name in ("delete_one", "drop_collection", "create_index", "run_command", "admin_command"):

        async def fn(session: ClientSession, name: str = name) -> Any:
            return await session.call_tool(name, {"collection": "run_tasks"})

        result = call(db, fn)
        assert result.isError, name
    assert db.calls() == []
