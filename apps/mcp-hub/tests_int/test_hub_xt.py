"""Cross-tenant cases for every hub tool (PRD §7.9 Testing; MASTER-PLAN §9 "every new MCP tool gets
a cross-tenant case"). Run by `make test-xt` (and `make test-int`).

Both orgs hold data. An `xt_agent` token for org A calls each tool through the hub, against the real
mcp-mongo and MongoDB, and must not read, list, count, update or infer anything of org B. The tool
list is enumerated from the running hub over MCP: a hub tool without a case in `HUB_TOOL_CASES`
fails `test_every_hub_tool_has_a_cross_tenant_case`, and so does a case for a tool that is gone.

Hook for T-3's suite (tools/tests_xt): `HUB_TOOL_CASES` keys are the hub's tool names; its
`MCP_SERVERS` can enumerate the hub with `onb_mcp_hub.server.hub_tools(platform_catalog())`.

The mock tools (MOCK-1) hold no tenant data in MongoDB; their tenant state is the idempotency store.
Their cases check that one org's idempotency key never answers, reveals or changes another org's record.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import CallToolResult

from .client import agent_claims, call, list_tools, new_run, text
from .conftest import Stack

A, B = "org_acme", "org_globex"
XT_SCOPES = (
    *(f"mongo__{t}" for t in ("list_collections", "find", "count", "aggregate", "insert_one", "update_one")),
    "mocks__create_bitbucket_account",
    "mocks__provision_laptop",
)


@dataclass(frozen=True)
class World:
    """One case's data: a few `run_tasks` in each org, tagged with a fresh marker."""

    marker: str
    root_db: Any

    def docs(self, org: str) -> list[dict[str, Any]]:
        return list(self.root_db["run_tasks"].find({"org_id": org, "marker": self.marker}))


def as_org(stack: Stack, org: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    result = call(stack, agent_claims("xt_agent", org, new_run(), XT_SCOPES), tool, args)
    assert result.isError is False, text(result)
    return result


def as_a(stack: Stack, tool: str, args: dict[str, Any]) -> CallToolResult:
    return as_org(stack, A, tool, args)


def case_list_collections(stack: Stack, world: World) -> None:
    result = as_a(stack, "mongo__list_collections", {})
    assert sorted(result.structuredContent["collections"]) == ["new_hires", "run_tasks"]  # type: ignore[index]


def case_find(stack: Stack, world: World) -> None:
    result = as_a(stack, "mongo__find", {"collection": "run_tasks", "filter": {"marker": world.marker}, "limit": 100})
    documents = result.structuredContent["documents"]  # type: ignore[index]
    assert len(documents) == 2
    assert {d["side"] for d in documents} == {"a"}
    b_id = str(world.docs(B)[0]["_id"])
    by_id = as_a(stack, "mongo__find", {"collection": "run_tasks", "filter": {"_id": {"$oid": b_id}}, "limit": 1})
    assert by_id.structuredContent == {"documents": [], "count": 0}


def case_count(stack: Stack, world: World) -> None:
    result = as_a(stack, "mongo__count", {"collection": "run_tasks", "filter": {"marker": world.marker}})
    assert result.structuredContent == {"count": 2}  # org B's three are not counted


def case_aggregate(stack: Stack, world: World) -> None:
    pipeline = [{"$match": {"marker": world.marker}}, {"$group": {"_id": "$side", "n": {"$sum": 1}}}]
    result = as_a(stack, "mongo__aggregate", {"collection": "run_tasks", "pipeline": pipeline})
    assert result.structuredContent["documents"] == [{"_id": "a", "n": 2}]  # type: ignore[index]


def case_insert_one(stack: Stack, world: World) -> None:
    as_a(stack, "mongo__insert_one", {"collection": "run_tasks", "document": {"marker": world.marker, "side": "new"}})
    assert len(world.docs(A)) == 3
    assert len(world.docs(B)) == 3  # nothing landed in org B
    assert all(d["org_id"] == A for d in world.docs(A))


def case_update_one(stack: Stack, world: World) -> None:
    args = {"collection": "run_tasks", "filter": {"marker": world.marker, "side": "b"}, "update": {"$set": {"x": 1}}}
    result = as_a(stack, "mongo__update_one", args)
    assert result.structuredContent == {"matched": 0, "modified": 0, "count": 0}
    assert all("x" not in d for d in world.docs(B))


def _mock_case(tool: str, args: dict[str, Any], id_field: str) -> Callable[[Stack, World], None]:
    """Org B uses an idempotency key first; org A's call with the same key gets its own record, sees
    nothing of B's, and leaves B's record exactly as it was."""

    def case(stack: Stack, world: World) -> None:
        task = {**args, "task_id": secrets.token_hex(12)}
        b_first = as_org(stack, B, tool, task).structuredContent
        before = (stack.mocks_store.count(A), stack.mocks_store.count(B))
        a_result = as_a(stack, tool, task)
        assert b_first is not None
        assert a_result.structuredContent is not None
        assert a_result.structuredContent[id_field] != b_first[id_field]
        assert b_first[id_field] not in text(a_result) + repr(a_result.structuredContent)
        assert (stack.mocks_store.count(A), stack.mocks_store.count(B)) == (before[0] + 1, before[1])
        assert as_org(stack, B, tool, task).structuredContent == b_first  # B's record unchanged

    return case


HIRE = "65f0aaaabbbbccccddddeeee"
HUB_TOOL_CASES: dict[str, Callable[[Stack, World], None]] = {
    "mocks__create_bitbucket_account": _mock_case(
        "mocks__create_bitbucket_account", {"hire_id": HIRE, "workspace": "xt-eng", "groups": []}, "account_id"
    ),
    "mocks__provision_laptop": _mock_case(
        "mocks__provision_laptop", {"hire_id": HIRE, "profile": "standard", "office": "xt-office"}, "order_id"
    ),
    "mongo__list_collections": case_list_collections,
    "mongo__find": case_find,
    "mongo__count": case_count,
    "mongo__aggregate": case_aggregate,
    "mongo__insert_one": case_insert_one,
    "mongo__update_one": case_update_one,
}


@pytest.fixture
def world(root_db: Any) -> World:
    marker = secrets.token_hex(6)
    root_db["run_tasks"].insert_many(
        [{"org_id": A, "marker": marker, "side": "a"} for _ in range(2)]
        + [{"org_id": B, "marker": marker, "side": "b"} for _ in range(3)]
    )
    return World(marker, root_db)


def test_every_hub_tool_has_a_cross_tenant_case(stack: Stack) -> None:
    listed = list_tools(stack, agent_claims("xt_agent", A, new_run(), XT_SCOPES))
    assert listed, "the hub listed its tools"
    assert sorted(set(listed) - set(HUB_TOOL_CASES)) == [], (
        "add a HUB_TOOL_CASES row in apps/mcp-hub/tests_int/test_hub_xt.py"
    )
    assert sorted(set(HUB_TOOL_CASES) - set(listed)) == [], "a case for a tool the hub no longer serves"
    print(f"\nmake test-xt: hub tools covered: {', '.join(sorted(listed))}")


@pytest.mark.parametrize("tool", sorted(HUB_TOOL_CASES))
def test_org_a_cannot_reach_org_b_through_hub_tool(tool: str, stack: Stack, world: World) -> None:
    HUB_TOOL_CASES[tool](stack, world)


def test_a_token_for_org_b_is_scoped_to_org_b(stack: Stack, world: World) -> None:
    """The mirror case: the scope follows the token, not a default org."""
    args = {"collection": "run_tasks", "filter": {"marker": world.marker}}
    result = call(stack, agent_claims("xt_agent", B, new_run(), XT_SCOPES), "mongo__count", args)
    assert result.structuredContent == {"count": 3}
