"""What the cross-tenant suite must cover: every API route, MCP tool and event consumer (PRD §7.9).

The suite enumerates the live surface from the real objects (the FastAPI app, the MCP servers, the
platform's event buses) and compares it with its case tables. Anything enumerated without a case
fails `make test-xt` (`uncovered`), and so does a case for something that no longer exists (`stale`).

Extension points, for the goals that add surface:

* **Routes** (REG-1, HIRE-1, TPL-2, …): nothing to register. `create_app()` is enumerated, so a new
  route fails the suite until `test_xt_api.py` has a `ROUTE_CASES` row for it. If `create_app` grows
  a new store argument, pass a Mongo-backed one in `conftest.py`'s `api` fixture.
* **MCP tools** (HUB-1, MCP-2): every server in `MCP_SERVERS` is enumerated through its own
  `list_tools`, so a new tool on mcp-mongo fails the suite until `test_xt_mcp.py` has a `TOOL_CASES`
  row. A module that builds a new MCP server goes in `MCP_SERVER_MODULES` and its server in
  `MCP_SERVERS`; `test_every_mcp_server_in_the_code_is_enumerated` fails until it does.
* **Event consumers** (RUN-1, …): every production subscription map goes in `PLATFORM_SUBSCRIPTIONS`
  and each consumer gets a `CONSUMER_CASES` row in `test_xt_consumers.py`.
  `test_every_event_bus_in_the_code_is_enumerated` fails when production code builds a
  `MongoEventBus` from a module not listed in `BUS_MODULES`.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from mcp.server.fastmcp import FastMCP
from onb_data import DataStore
from onb_mcp_mongo.server import build_server

ROOT = Path(__file__).resolve().parents[2]

# --- event consumers --------------------------------------------------------------------------------

# Every subscription map the platform runs, by owner. Empty until the orchestrator (RUN-1) subscribes
# its first consumer; that goal adds its map here (imported from its module) with a case per consumer.
PLATFORM_SUBSCRIPTIONS: Mapping[str, Mapping[str, Sequence[str]]] = {}

# Production modules that build a `MongoEventBus`, relative to the repo root. Each must feed its
# subscriptions into PLATFORM_SUBSCRIPTIONS above.
BUS_MODULES: frozenset[str] = frozenset()

# --- MCP servers ------------------------------------------------------------------------------------


def _mcp_mongo(store: DataStore, context_key: str) -> FastMCP:
    return build_server(store, context_key)


# Every MCP server the platform runs, by name, built over a store. HUB-1 adds the hub; the mocks add
# theirs (and their tools then need cases).
MCP_SERVERS: Mapping[str, Callable[[DataStore, str], FastMCP]] = {"mcp-mongo": _mcp_mongo}

# Production modules that build an MCP server (`FastMCP(...)`), relative to the repo root.
MCP_SERVER_MODULES: frozenset[str] = frozenset({"apps/mcp-mongo/src/onb_mcp_mongo/server.py"})

# Production code lives here; tests, the py-eventbus library itself and its docstrings do not count.
_SOURCE_GLOBS = ("apps/*/src/**/*.py", "libs/*/src/**/*.py")
_NOT_PRODUCTION = ("libs/py-eventbus/src/",)


def api_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) the app serves, sorted. HEAD/OPTIONS that Starlette adds are not routes."""
    keys = {
        (method, context.original_route.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        for method in context.original_route.methods or ()
    }
    return sorted(keys)


def mcp_tools(store: DataStore, context_key: str) -> list[tuple[str, str]]:
    """Every (server, tool) pair, as each server lists its tools to a client."""

    async def listed() -> list[tuple[str, str]]:
        return [
            (name, tool.name)
            for name, build in MCP_SERVERS.items()
            for tool in await build(store, context_key).list_tools()
        ]

    return sorted(asyncio.run(listed()))


def platform_consumers(subscriptions: Mapping[str, Mapping[str, Sequence[str]]]) -> list[str]:
    return sorted({consumer for owned in subscriptions.values() for names in owned.values() for consumer in names})


def uncovered[K](surface: Iterable[K], cases: Collection[K]) -> list[K]:
    """Enumerated surface with no case: each one fails the suite."""
    return sorted(set(surface) - set(cases), key=str)


def stale[K](surface: Iterable[K], cases: Collection[K]) -> list[K]:
    """Cases for surface that no longer exists: a renamed route would otherwise lose its case quietly."""
    return sorted(set(cases) - set(surface), key=str)


def _calls(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called == name:
                return True
    return False


def modules_calling(name: str, root: Path = ROOT) -> set[str]:
    """Production modules that call `name(...)` (a constructor), relative to `root`."""
    found: set[str] = set()
    for pattern in _SOURCE_GLOBS:
        for path in root.glob(pattern):
            rel = path.relative_to(root).as_posix()
            if not rel.startswith(_NOT_PRODUCTION) and _calls(path, name):
                found.add(rel)
    return found
