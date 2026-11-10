"""No destructive or admin tool exists (MCP-2, PRD §7.5: "No delete, drop, index, admin or
`runCommand` tools"). Checked on the served tool list, on the contracts and handlers the server is
built from, and on the source, so a new tool or a driver call for one cannot slip in."""

from __future__ import annotations

import ast
import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from onb_mcp_mongo import main as main_module
from onb_mcp_mongo.contracts import CONTRACTS
from onb_mcp_mongo.server import HANDLERS

from .fakes import FakeDatabase, make_store
from .harness import call

PRD_TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}
FORBIDDEN_WORDS = ("delete", "remove", "drop", "index", "admin", "command", "runcommand", "eval", "rename", "replace")
PACKAGE = "onb_mcp_mongo"
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE
TOOL_ENTRY = "server"
# MCP-3's database-user provisioning and start-up privilege check: admin code by design, run by
# `main` and the provisioning CLI, never reachable from a tool (asserted below).
ADMIN_MODULES = frozenset({"db_users"})
# The exemption is by exact path, so a file of the same name elsewhere is still scanned.
ADMIN_FILES = frozenset({(SRC / "db_users.py").resolve()})
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


def _local_imports(module: str) -> set[str]:
    """The `onb_mcp_mongo` modules `module` imports, from its source (any `import` statement,
    at any depth, including `from . import x` and absolute `onb_mcp_mongo.x`)."""
    tree = ast.parse((SRC / f"{module}.py").read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 or node.module == PACKAGE or (node.module or "").startswith(PACKAGE + "."):
                base = (node.module or "").removeprefix(PACKAGE).removeprefix(".")
                if base:
                    found.add(base.split(".")[0])
                else:
                    found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE + "."):
                    found.add(alias.name.split(".")[1])
    return {name for name in found if (SRC / f"{name}.py").exists()}


def tool_closure() -> set[str]:
    """Every module reachable by import from the server that serves the tools."""
    seen: set[str] = set()
    todo = [TOOL_ENTRY]
    while todo:
        module = todo.pop()
        if module not in seen:
            seen.add(module)
            todo.extend(_local_imports(module) - seen)
    return seen


def test_the_tool_closure_holds_the_tool_code_and_not_the_admin_code() -> None:
    closure = tool_closure()
    # Every handler and contract the server serves lives in the closure, so the scan below covers it.
    served = {fn.__module__.rsplit(".", 1)[-1] for fn in HANDLERS.values()}
    served |= {contract.args.__module__.rsplit(".", 1)[-1] for contract in CONTRACTS.values()}
    assert served <= closure
    assert {"server", "tools", "contracts", "policy", "validator", "validator_rules"} <= closure
    # Provisioning (MCP-3) runs .command() for roles and the start-up privilege check; no tool reaches it.
    assert closure.isdisjoint(ADMIN_MODULES)


def test_importing_the_tools_never_loads_the_admin_code() -> None:
    """At run time too (catches dynamic imports): a fresh interpreter that imports the server and
    every tool module has not loaded the provisioning module."""
    modules = sorted(tool_closure())
    script = (
        "import importlib, sys\n"
        f"for name in {modules!r}: importlib.import_module('{PACKAGE}.' + name)\n"
        f"print(sorted(m for m in sys.modules if m.startswith('{PACKAGE}.')))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    loaded = {name.split(".")[1] for name in ast.literal_eval(out.strip())}
    assert loaded, out
    assert loaded.isdisjoint(ADMIN_MODULES), loaded


def test_no_source_file_but_the_admin_file_calls_a_delete_drop_index_or_command_method() -> None:
    """Every file under src/ is scanned, at any depth, except the one admin file by exact path,
    which the tests above keep out of the tools' reach."""
    files = sorted(path for path in SRC.rglob("*.py") if path.resolve() not in ADMIN_FILES)
    assert {SRC / f"{name}.py" for name in tool_closure()} <= set(files)
    hits = [
        f"{path.relative_to(SRC)}: {m.group(0)}" for path in files for m in FORBIDDEN_CALLS.finditer(path.read_text())
    ]
    assert hits == []


def test_the_admin_exemption_is_exactly_the_provisioning_file() -> None:
    assert frozenset({(SRC / "db_users.py").resolve()}) == ADMIN_FILES
    assert all(path.is_file() for path in ADMIN_FILES)


def test_the_package_has_no_subpackages() -> None:
    """The import closure follows top-level modules only; a subpackage would need it extended."""
    nested = [path.relative_to(SRC) for path in SRC.rglob("*.py") if path.parent != SRC]
    assert nested == []


# --- what production serves -------------------------------------------------------------------

_REGISTERING_ATTRS = frozenset({"add_tool", "tool", "remove_tool", "add_resource", "resource", "prompt"})
_SERVER_CONSTRUCTORS = frozenset({"FastMCP", "Server", "Tool", "TracedTool", "FuncMetadata"})


def _registrations(path: Path) -> list[str]:
    """Calls that build an MCP server or register a tool, resource or prompt (decorators too)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        targets = [node.func] if isinstance(node, ast.Call) else []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            targets += node.decorator_list
        for target in targets:
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if (isinstance(target, ast.Attribute) and name in _REGISTERING_ATTRS) or name in _SERVER_CONSTRUCTORS:
                found.append(f"{path.relative_to(SRC)}:{getattr(target, 'lineno', '?')} {name}")
    return found


def test_only_the_server_module_builds_the_mcp_server_or_registers_tools() -> None:
    others = sorted(path for path in SRC.rglob("*.py") if path != SRC / f"{TOOL_ENTRY}.py")
    assert [hit for path in others for hit in _registrations(path)] == []
    assert _registrations(SRC / f"{TOOL_ENTRY}.py")  # the rule is not vacuous: server.py does build it


_FORBIDDEN_WORD = re.compile(
    r"\b(delete|remove|drop|index(es)?|admin|run_?command|command|eval|rename|replace)\b", re.I
)


async def _tools_over_mcp(app: Any) -> list[Any]:
    """List tools through a real MCP client session against `app`, running the app's own lifespan
    (which starts its session manager), exactly as uvicorn would serve it."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://mcp-mongo") as http,
            streamable_http_client("http://mcp-mongo/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return list((await session.list_tools()).tools)


def _served_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run the real entry point with only its I/O patched and return the exact app it hands to
    uvicorn.run: whatever main() builds, wraps or substitutes is what gets checked."""
    served: list[Any] = []
    monkeypatch.setattr(main_module, "assert_least_privilege", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "init_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "connect", lambda *a, **k: make_store(FakeDatabase()))
    monkeypatch.setattr("uvicorn.run", lambda app, *a, **k: served.append(app))
    main_module.main(
        {"MCP_MONGO_URI": "mongodb://r@h/", "MCP_MONGO_WRITE_URI": "mongodb://w@h/", "MCP_CALL_CONTEXT_KEY": "k" * 40}
    )
    assert len(served) == 1
    return served[0]


def test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Box 4, behaviourally: what production serves, seen by a real MCP client, has exactly the six
    PRD tools, none named or described as delete/drop/index/admin/runCommand, none destructive."""
    tools = asyncio.run(_tools_over_mcp(_served_app(monkeypatch)))
    assert {tool.name for tool in tools} == PRD_TOOLS
    for tool in tools:
        assert not _FORBIDDEN_WORD.search(tool.name), tool.name
        assert not _FORBIDDEN_WORD.search(tool.description or ""), tool.name
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is False, tool.name


def test_calling_a_tool_that_does_not_exist_is_an_error(db: FakeDatabase) -> None:
    for name in ("delete_one", "drop_collection", "create_index", "run_command", "admin_command"):

        async def fn(session: ClientSession, name: str = name) -> Any:
            return await session.call_tool(name, {"collection": "run_tasks"})

        result = call(db, fn)
        assert result.isError, name
    assert db.calls() == []
