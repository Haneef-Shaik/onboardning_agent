"""The suite covers the whole surface: a route, MCP tool or event consumer without a case fails
`make test-xt` (PRD §7.9 Testing; MASTER-PLAN §9 "every new route, MCP tool and event consumer gets
a cross-tenant case"). So does a case left over for something that is gone.

These tests need no database: they enumerate the app, the MCP servers and the subscription maps
from the real objects (built over a lazy store that never connects).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from onb_data import DataStore, connect
from onb_data.safety_net import TenantCommandListener
from onb_data.safety_net_pytest import GUARD_FIXTURE
from pymongo import monitoring

from .conftest import MCP_CONTEXT_KEY, build_api
from .surface import (
    BUS_MODULES,
    MCP_SERVER_MODULES,
    PLATFORM_SUBSCRIPTIONS,
    api_routes,
    mcp_tools,
    modules_calling,
    platform_consumers,
    stale,
    uncovered,
)
from .test_xt_api import ROUTE_CASES
from .test_xt_consumers import CONSUMER_CASES
from .test_xt_mcp import TOOL_CASES


@pytest.fixture(scope="module")
def offline() -> DataStore:
    return connect("mongodb://127.0.0.1:1", "xt_offline")  # lazy: never connects


def test_every_api_route_has_a_cross_tenant_case(offline: DataStore) -> None:
    routes = api_routes(build_api(offline))
    assert routes, "the enumerator found the app's routes"
    assert uncovered(routes, ROUTE_CASES) == [], "add a ROUTE_CASES row in tools/tests_xt/test_xt_api.py"
    assert stale(routes, ROUTE_CASES) == []


def test_every_mcp_tool_has_a_cross_tenant_case(offline: DataStore) -> None:
    tools = mcp_tools(offline, MCP_CONTEXT_KEY)
    assert tools, "the enumerator found the servers' tools"
    assert uncovered(tools, TOOL_CASES) == [], "add a TOOL_CASES row in tools/tests_xt/test_xt_mcp.py"
    assert stale(tools, TOOL_CASES) == []


def test_every_event_consumer_has_a_cross_tenant_case() -> None:
    consumers = platform_consumers(PLATFORM_SUBSCRIPTIONS)
    assert uncovered(consumers, CONSUMER_CASES) == [], "add a CONSUMER_CASES row in tools/tests_xt/test_xt_consumers.py"
    assert stale(consumers, CONSUMER_CASES) == []


def test_every_event_bus_in_the_code_is_enumerated() -> None:
    """A module that builds a `MongoEventBus` must feed its subscriptions to PLATFORM_SUBSCRIPTIONS."""
    assert modules_calling("MongoEventBus") == BUS_MODULES


def test_every_mcp_server_in_the_code_is_enumerated() -> None:
    assert modules_calling("FastMCP") == MCP_SERVER_MODULES


# --- the coverage helpers themselves -------------------------------------------------------------------


def test_uncovered_and_stale_name_the_difference() -> None:
    surface = [("GET", "/a"), ("POST", "/b")]
    cases = {("GET", "/a"): None, ("GET", "/gone"): None}
    assert uncovered(surface, cases) == [("POST", "/b")]
    assert stale(surface, cases) == [("GET", "/gone")]


def test_a_route_added_without_a_case_is_uncovered(offline: DataStore) -> None:
    app = build_api(offline)

    @app.get("/xt-dummy")
    async def dummy() -> dict[str, str]:
        return {}

    assert uncovered(api_routes(app), ROUTE_CASES) == [("GET", "/xt-dummy")]


def test_the_scan_finds_a_constructor_call(tmp_path: Path) -> None:
    module = tmp_path / "apps" / "worker" / "src" / "onb_worker" / "main.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from onb_eventbus import MongoEventBus\nbus = onb_eventbus.MongoEventBus(store, subscriptions={})\n"
    )
    (tmp_path / "libs" / "py-eventbus" / "src").mkdir(parents=True)
    (tmp_path / "libs" / "py-eventbus" / "src" / "bus.py").write_text("x = MongoEventBus(store)\n")
    assert modules_calling("MongoEventBus", tmp_path) == {"apps/worker/src/onb_worker/main.py"}


# --- the T-2 safety net is on for every test of this suite ------------------------------------------------


def test_every_cross_tenant_test_runs_under_the_safety_net_guard(request: pytest.FixtureRequest) -> None:
    items = request.session.items
    assert items, "the session collected the suite"
    unguarded = [item.nodeid for item in items if GUARD_FIXTURE not in getattr(item, "fixturenames", ())]
    assert unguarded == []


def test_the_listener_is_registered_globally(tenant_safety_net: TenantCommandListener) -> None:
    assert monitoring._LISTENERS.command_listeners.count(tenant_safety_net) == 1
