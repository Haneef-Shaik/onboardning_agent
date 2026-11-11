"""Every MCP tool: an org-A agent cannot read, list, update or infer org-B data (PRD §7.9 Testing, §7.5).

mcp-mongo runs over HTTP on loopback against the seeded scratch database; each call carries a
hub-style call context for org A. `TOOL_CASES` has one row per (server, tool) the servers list;
`test_xt_coverage.py` fails the run when a tool has no row. Every row also gets the generic checks:
its ordinary call returns none of org B's strings, and an `org_id` argument naming org B is refused
without echoing anything of org B. Org B's documents are compared before and after every case.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from .conftest import McpServer, Unscoped, World
from .mcp_client import READ, WRITE, Outcome, call
from .world import ORG_A, ORG_B, Seen, assert_indistinguishable, assert_no_leak, tenant_documents

Args = dict[str, Any]
Call = Callable[[str, Args], Outcome]  # (tool, args) -> outcome, as an org-A agent

B_HIRE, B_RUN = "h_globex_001", "run_globex_001"
MISSING_HIRE, MISSING_RUN = "h_nobody_xt", "run_nobody_xt"


def _seen(outcome: Outcome) -> Seen:
    return Seen(int(outcome.error), outcome.data if not outcome.error else outcome.text)


@dataclass(frozen=True)
class ToolCase:
    args: Args
    """The ordinary call an org-A agent makes."""
    check: Callable[[Call, World, Unscoped], None]
    """The tool's own cross-tenant assertions."""


# MCP-2's validator refuses any filter, pipeline or document that names `org_id`, even as `$org_id`.
NAMES_ORG_REFUSAL = "may not name org_id: tenant scope comes from the hub"


def _assert_refused_for_naming_org(outcome: Outcome, what: str) -> None:
    """A call that names an org is refused before it reads anything: no data, MCP-2's reason, and no
    trace of org B in the message."""
    assert outcome.error, f"{what}: answered {outcome.data}"
    assert outcome.data is None, f"{what}: a refusal carried data"
    assert NAMES_ORG_REFUSAL in outcome.text, outcome.text
    assert_no_leak(outcome.text, what)


def _a_count(collection: str) -> int:
    return len(tenant_documents(ORG_A)[collection])


# --- list_collections ------------------------------------------------------------------------------------


def _list_collections_check(agent: Call, _: World, __: Unscoped) -> None:
    """Only the context's collections, and nothing that depends on what org B stores."""
    assert agent("list_collections", {}).ok() == {"collections": sorted(READ | WRITE)}


# --- find ------------------------------------------------------------------------------------------------


def _find_check(agent: Call, _: World, __: Unscoped) -> None:
    for collection in sorted(READ):
        found = agent("find", {"collection": collection, "filter": {}, "limit": 100}).ok()
        assert {d["org_id"] for d in found["documents"]} <= {ORG_A}
        assert found["count"] == len(found["documents"]) == _a_count(collection)
    foreign = agent("find", {"collection": "new_hires", "filter": {"_id": B_HIRE}, "limit": 1})
    missing = agent("find", {"collection": "new_hires", "filter": {"_id": MISSING_HIRE}, "limit": 1})
    assert_indistinguishable(_seen(foreign), _seen(missing), "find by org B's id")
    by_field = agent("find", {"collection": "run_tasks", "filter": {"run_id": B_RUN}, "limit": 100})
    assert by_field.ok()["documents"] == []
    for collection in sorted(READ):
        named = agent("find", {"collection": collection, "filter": {"org_id": ORG_B}, "limit": 100})
        _assert_refused_for_naming_org(named, f"find on {collection} naming org B in the filter")
    by_expr = {"$expr": {"$eq": ["$org_id", ORG_B]}}
    by_expr_found = agent("find", {"collection": "new_hires", "filter": by_expr, "limit": 100})
    _assert_refused_for_naming_org(by_expr_found, "find naming org B through $org_id")


# --- count -----------------------------------------------------------------------------------------------


def _count_check(agent: Call, _: World, __: Unscoped) -> None:
    for collection in sorted(READ):
        assert agent("count", {"collection": collection}).ok() == {"count": _a_count(collection)}
    foreign = agent("count", {"collection": "new_hires", "filter": {"_id": B_HIRE}})
    missing = agent("count", {"collection": "new_hires", "filter": {"_id": MISSING_HIRE}})
    assert foreign.ok() == {"count": 0}
    assert_indistinguishable(_seen(foreign), _seen(missing), "count of org B's id")
    named = agent("count", {"collection": "new_hires", "filter": {"$or": [{"org_id": ORG_B}]}})
    _assert_refused_for_naming_org(named, "count naming org B in the filter")
    by_expr = {"$expr": {"$eq": ["$org_id", ORG_B]}}
    by_expr_counted = agent("count", {"collection": "new_hires", "filter": by_expr})
    _assert_refused_for_naming_org(by_expr_counted, "count naming org B through $org_id")


# --- aggregate -------------------------------------------------------------------------------------------


def _aggregate_check(agent: Call, _: World, __: Unscoped) -> None:
    total = [{"$group": {"_id": None, "n": {"$sum": 1}}}]
    for collection in sorted(READ):
        documents = agent("aggregate", {"collection": collection, "pipeline": total}).ok()["documents"]
        assert documents == ([{"_id": None, "n": _a_count(collection)}] if _a_count(collection) else [])
    foreign = agent("aggregate", {"collection": "run_tasks", "pipeline": [{"$match": {"run_id": B_RUN}}]})
    missing = agent("aggregate", {"collection": "run_tasks", "pipeline": [{"$match": {"run_id": MISSING_RUN}}]})
    assert_indistinguishable(_seen(foreign), _seen(missing), "aggregate over org B's run")
    named = agent("aggregate", {"collection": "run_tasks", "pipeline": [{"$match": {"org_id": ORG_B}}]})
    _assert_refused_for_naming_org(named, "aggregate naming org B in $match")
    by_org = agent("aggregate", {"collection": "run_tasks", "pipeline": [{"$group": {"_id": "$org_id"}}]})
    _assert_refused_for_naming_org(by_org, "aggregate grouping by $org_id")
    # Stages that read another collection would leave the org's scope: refused.
    for stage in (
        {"$lookup": {"from": "new_hires", "localField": "run_id", "foreignField": "_id", "as": "x"}},
        {
            "$graphLookup": {
                "from": "new_hires",
                "startWith": "$run_id",
                "connectFromField": "_id",
                "connectToField": "_id",
                "as": "x",
            }
        },
        {"$unionWith": {"coll": "new_hires"}},
        {"$facet": {"x": [{"$lookup": {"from": "new_hires", "localField": "_id", "foreignField": "_id", "as": "y"}}]}},
        {"$out": "xt_out"},
        {"$merge": {"into": "xt_out"}},
    ):
        refused = agent("aggregate", {"collection": "run_tasks", "pipeline": [stage]})
        assert refused.error, refused.text
        assert_no_leak(refused.text, "a refused cross-collection stage")


# --- insert_one ------------------------------------------------------------------------------------------


def _insert_one_check(agent: Call, xt: World, unscoped: Unscoped) -> None:
    created = agent("insert_one", {"collection": "run_tasks", "document": {"task_id": "xt_new", "run_id": B_RUN}}).ok()
    with unscoped("root read-back by marker, without assuming the org, proves which org the tool stamped"):
        stored = list(xt.root_db["run_tasks"].find({"task_id": "xt_new"}))
    assert [(str(d["_id"]), d["org_id"]) for d in stored] == [(created["inserted_id"], ORG_A)]
    refused = agent("insert_one", {"collection": "run_tasks", "document": {"task_id": "xt_b", "org_id": ORG_B}})
    assert refused.error
    assert "org_id" in refused.text
    with unscoped("root read-back proves the refused insert wrote nothing in any org"):
        assert xt.root_db["run_tasks"].count_documents({"task_id": "xt_b"}) == 0
    # `_id` is unique across every org: choosing org B's id must answer exactly like a fresh one.
    taken = agent("insert_one", {"collection": "approvals", "document": {"_id": "appr_globex_xt", "status": "x"}})
    fresh = agent("insert_one", {"collection": "approvals", "document": {"_id": "appr_nobody_xt", "status": "x"}})
    assert taken.error
    assert_indistinguishable(_seen(taken), _seen(fresh), "insert_one with org B's _id")


# --- update_one ------------------------------------------------------------------------------------------


def _update_one_check(agent: Call, _: World, __: Unscoped) -> None:
    hijack = {"$set": {"status": "xt_hijacked"}}
    foreign = agent("update_one", {"collection": "run_tasks", "filter": {"run_id": B_RUN}, "update": hijack})
    missing = agent("update_one", {"collection": "run_tasks", "filter": {"run_id": MISSING_RUN}, "update": hijack})
    assert foreign.ok() == {"matched": 0, "modified": 0, "count": 0}
    assert_indistinguishable(_seen(foreign), _seen(missing), "update of org B's run")
    move = {"$set": {"org_id": ORG_B}}
    refused = agent("update_one", {"collection": "run_tasks", "filter": {"run_id": "run_acme_001"}, "update": move})
    assert refused.error
    assert "org_id" in refused.text


TOOL_CASES: Mapping[tuple[str, str], ToolCase] = {
    ("mcp-mongo", "list_collections"): ToolCase({}, _list_collections_check),
    ("mcp-mongo", "find"): ToolCase({"collection": "new_hires", "filter": {}, "limit": 100}, _find_check),
    ("mcp-mongo", "count"): ToolCase({"collection": "run_tasks"}, _count_check),
    ("mcp-mongo", "aggregate"): ToolCase(
        {"collection": "onboarding_runs", "pipeline": [{"$sort": {"_id": 1}}]}, _aggregate_check
    ),
    ("mcp-mongo", "insert_one"): ToolCase({"collection": "approvals", "document": {"state": "xt"}}, _insert_one_check),
    ("mcp-mongo", "update_one"): ToolCase(
        {"collection": "run_tasks", "filter": {"run_id": "run_acme_001"}, "update": {"$set": {"note": "xt"}}},
        _update_one_check,
    ),
}

_SERVERS: Mapping[str, str] = {"mcp-mongo": "mcp_mongo"}  # server name -> its conftest fixture
_TOOL_IDS = [f"{server}.{tool}" for server, tool in TOOL_CASES]


def _agent(request: pytest.FixtureRequest, server: str) -> Call:
    mcp: McpServer = request.getfixturevalue(_SERVERS[server])
    return lambda tool, args: call(mcp.url, mcp.key, ORG_A, tool, args)


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_the_tools_own_cross_tenant_checks(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    before = xt_world.org_b_snapshot(unscoped)
    TOOL_CASES[key].check(_agent(request, key[0]), xt_world, unscoped)
    assert xt_world.org_b_snapshot(unscoped) == before


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_no_org_b_data_reaches_an_org_a_agent(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    before = xt_world.org_b_snapshot(unscoped)
    outcome = _agent(request, key[0])(key[1], TOOL_CASES[key].args)
    outcome.ok()
    assert_no_leak(repr(outcome.data) + outcome.text, f"{key[0]}.{key[1]}")
    assert xt_world.org_b_snapshot(unscoped) == before


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_an_org_id_argument_naming_org_b_is_refused(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    before = xt_world.org_b_snapshot(unscoped)
    outcome = _agent(request, key[0])(key[1], {**TOOL_CASES[key].args, "org_id": ORG_B})
    assert outcome.error, outcome.data
    assert "org_id" in outcome.text
    assert_no_leak(outcome.text, f"{key[0]}.{key[1]} refusing org_id")
    assert xt_world.org_b_snapshot(unscoped) == before
