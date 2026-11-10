"""No destructive or admin tool exists (MCP-2, PRD §7.5: "No delete, drop, index, admin or
`runCommand` tools"). Checked on the served tool list, on the contracts and handlers the server is
built from, and on the source, so a new tool or a driver call for one cannot slip in."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from onb_mcp_mongo.contracts import CONTRACTS
from onb_mcp_mongo.server import HANDLERS

from .fakes import FakeDatabase
from .harness import call

PRD_TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}
FORBIDDEN_WORDS = ("delete", "remove", "drop", "index", "admin", "command", "runcommand", "eval", "rename", "replace")
PACKAGE = "onb_mcp_mongo"
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE
TOOL_ENTRY = "server"
# MCP-3's database-user provisioning and start-up privilege check: admin code by design, run by
# `main` and the provisioning CLI, never reachable from a tool (asserted below).
ADMIN_MODULES = frozenset({"db_users"})
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


def test_no_source_module_but_the_named_admin_code_calls_a_delete_drop_index_or_command_method() -> None:
    """Every module is scanned except the named admin modules, which the tests above keep out of
    the tools' reach; so the scan covers the whole tool closure and any new module."""
    files = sorted(path for path in SRC.rglob("*.py") if path.stem not in ADMIN_MODULES)
    scanned = {path.stem for path in files}
    assert tool_closure() <= scanned
    hits = [f"{path.name}: {m.group(0)}" for path in files for m in FORBIDDEN_CALLS.finditer(path.read_text())]
    assert hits == []


def test_the_admin_exemption_is_only_the_provisioning_module() -> None:
    assert frozenset({"db_users"}) == ADMIN_MODULES
    assert all((SRC / f"{name}.py").exists() for name in ADMIN_MODULES)


def test_calling_a_tool_that_does_not_exist_is_an_error(db: FakeDatabase) -> None:
    for name in ("delete_one", "drop_collection", "create_index", "run_command", "admin_command"):

        async def fn(session: ClientSession, name: str = name) -> Any:
            return await session.call_tool(name, {"collection": "run_tasks"})

        result = call(db, fn)
        assert result.isError, name
    assert db.calls() == []
