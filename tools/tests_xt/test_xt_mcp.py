"""Every MCP tool: an org-A agent cannot read, list, update or infer org-B data (PRD §7.9 Testing, §7.5).

mcp-mongo runs over HTTP on loopback against the seeded scratch database, and mcp-mocks (MOCK-1)
over its in-memory idempotency store; each call carries a hub-style call context for org A, signed
with that server's own key for its own audience. `TOOL_CASES` has one row per (server, tool) the servers list;
`test_xt_coverage.py` fails the run when a tool has no row. Every row also gets the generic checks:
its ordinary call returns none of org B's strings, and an `org_id` argument naming org B is refused
without echoing anything of org B. Org B's documents are compared before and after every case.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import pytest
from onb_agents.runtime.task import GRANT_HEADER
from onb_crypto import GrantIssuer, VaultField, load_sink_policy
from onb_crypto.tokens import new_token
from onb_data import tenant_scope

from . import world
from .conftest import McpServer, Unscoped, World
from .mcp_client import READ, WRITE, Outcome, call
from .surface import ROOT, XT_GRANT_KEY
from .world import ORG_A, ORG_B, Seen, assert_indistinguishable, assert_no_leak, tenant_documents

Args = dict[str, Any]


class Call(Protocol):
    """(tool, args) -> outcome, as an org-A agent (or, where a case needs it, as `org`). `grant` is the
    task's PII grant, sent as the hub forwards it to a sink (NOTIF-1)."""

    def __call__(self, tool: str, args: Args, org: str = ORG_A, *, grant: str | None = None) -> Outcome: ...


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
    prepare: Callable[[World], object] | None = None
    """Seeding the case needs before org B is snapshotted (NOTIF-1: both orgs' vault hires)."""


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


# --- mcp-mocks (MOCK-1): tenant state is the idempotency store, keyed by org --------------------------

MOCK_HIRE = "65f0aaaabbbbccccddddeeee"
BITBUCKET_ARGS: Args = {"task_id": "65f0a1b2c3d4e5f6a7b8c9d0", "hire_id": MOCK_HIRE, "workspace": "xt-eng"}
LAPTOP_ARGS: Args = {
    "task_id": "65f0a1b2c3d4e5f6a7b8c9d1",
    "hire_id": MOCK_HIRE,
    "profile": "standard",
    "office": "xt-office",
}


def _mock_check(tool: str, args: Args, id_field: str) -> Callable[[Call, World, Unscoped], None]:
    """Org B uses an idempotency key first. Org A's call with the same key gets a record of its own,
    sees nothing of B's, and B's record answers exactly as before."""

    def check(agent: Call, _: World, __: Unscoped) -> None:
        task = {**args, "task_id": secrets.token_hex(12)}
        b_first = agent(tool, task, org=ORG_B).ok()
        a_result = agent(tool, task)
        a_seen = repr(a_result.ok()) + a_result.text
        assert a_result.ok()[id_field] != b_first[id_field]
        assert b_first[id_field] not in a_seen
        assert agent(tool, task).ok() == a_result.ok()  # A's replay answers A's record
        assert agent(tool, task, org=ORG_B).ok() == b_first  # B's record is unchanged

    return check


# --- mcp-notify (NOTIF-1): the sink resolves a token only for the caller's org, hire and grant ------------

POLICY = load_sink_policy(ROOT / "config" / "pii_sink_policy.yaml")
NOTIFY_ARGS: Args = {"channel": "in_app", "recipient_user_id": "u_acme_hr1", "body": "Welcome sent."}
ABSENT_REF = new_token()  # well-formed, stored nowhere


def _vault_hire(xt: World, unscoped: Unscoped, org: str) -> tuple[str, str]:
    """(hire id, mobile token) of the org's vault-backed hire: a token, never a value."""
    hire_id = xt.vault_hires()[org]
    with unscoped("root read of a seeded hire's mobile token, to address it like the orchestrator would"):
        hire = xt.root_db["new_hires"].find_one({"_id": hire_id})
    assert hire is not None
    return hire_id, str(hire["pii"]["mobile"])


def _grant(org: str, hire_id: str) -> str:
    """A grant as the orchestrator issues it for a welcome task (RUN-3), signed with this run's key."""
    with tenant_scope(org):
        return GrantIssuer(XT_GRANT_KEY, POLICY).issue_grant(
            org,
            run_id=f"run_xt_{org}",
            task_id="xt_welcome",
            subject_id=hire_id,
            fields=[VaultField.MOBILE],
            sink="notify.telegram",
        )


def _notify_check(agent: Call, xt: World, unscoped: Unscoped) -> None:
    """Org A sends to its own hire; org B's token under org A's grant, and org B's grant, resolve nothing
    and answer exactly like a token that does not exist. An in-app message to an org-B user answers like
    one to nobody. No outbox copy, in any org, holds a raw mobile."""
    a_hire, a_token = _vault_hire(xt, unscoped, ORG_A)
    b_hire, b_token = _vault_hire(xt, unscoped, ORG_B)
    body = {"channel": "telegram", "body": "Welcome aboard!"}
    agent("notify_send", {**body, "recipient_token": a_token}, grant=_grant(ORG_A, a_hire)).ok()
    foreign = agent("notify_send", {**body, "recipient_token": b_token}, grant=_grant(ORG_A, a_hire))
    absent = agent("notify_send", {**body, "recipient_token": ABSENT_REF}, grant=_grant(ORG_A, a_hire))
    assert foreign.error
    assert_indistinguishable(_seen(foreign), _seen(absent), "notify_send to org B's token")
    b_grant = agent("notify_send", {**body, "recipient_token": b_token}, grant=_grant(ORG_B, b_hire))
    assert b_grant.error
    assert "wrong_org" in b_grant.text
    for refused in (foreign, b_grant):
        assert_no_leak(refused.text, "a refused notify_send")
    to_b_user = agent("notify_send", {**NOTIFY_ARGS, "recipient_user_id": "u_globex_hr1"})
    to_nobody = agent("notify_send", {**NOTIFY_ARGS, "recipient_user_id": "u_nobody_xt"})
    assert_indistinguishable(_seen(to_b_user), _seen(to_nobody), "notify_send in-app to an org-B user")
    with unscoped("root read-back of every outbox copy, in every org"):
        outbox = list(xt.root_db["mock_outbox"].find())
    assert [(d["org_id"], d["recipient"]["token"]) for d in outbox] == [(ORG_A, a_token)]
    raw = [s for org in (ORG_A, ORG_B) for s in world.xt_vault_strings(org)]
    assert not any(s in repr(outbox) for s in raw)


TOOL_CASES: Mapping[tuple[str, str], ToolCase] = {
    ("mcp-notify", "notify_send"): ToolCase(NOTIFY_ARGS, _notify_check, prepare=World.vault_hires),
    ("mcp-mocks", "create_bitbucket_account"): ToolCase(
        BITBUCKET_ARGS, _mock_check("create_bitbucket_account", BITBUCKET_ARGS, "account_id")
    ),
    ("mcp-mocks", "provision_laptop"): ToolCase(LAPTOP_ARGS, _mock_check("provision_laptop", LAPTOP_ARGS, "order_id")),
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

_SERVERS: Mapping[str, str] = {  # server -> its fixture
    "mcp-mongo": "mcp_mongo",
    "mcp-mocks": "mcp_mocks",
    "mcp-notify": "mcp_notify",
}
_TOOL_IDS = [f"{server}.{tool}" for server, tool in TOOL_CASES]


def _prepare(xt: World, key: tuple[str, str]) -> None:
    prepare = TOOL_CASES[key].prepare
    if prepare is not None:
        prepare(xt)


def _agent(request: pytest.FixtureRequest, server: str) -> Call:
    mcp: McpServer = request.getfixturevalue(_SERVERS[server])

    def agent(tool: str, args: Args, org: str = ORG_A, *, grant: str | None = None) -> Outcome:
        headers = {GRANT_HEADER: grant} if grant is not None else None
        return call(mcp.url, mcp.key, org, tool, args, audience=mcp.audience, headers=headers)

    return agent


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_the_tools_own_cross_tenant_checks(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    _prepare(xt_world, key)
    before = xt_world.org_b_snapshot(unscoped)
    TOOL_CASES[key].check(_agent(request, key[0]), xt_world, unscoped)
    assert xt_world.org_b_snapshot(unscoped) == before


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_no_org_b_data_reaches_an_org_a_agent(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    _prepare(xt_world, key)
    before = xt_world.org_b_snapshot(unscoped)
    outcome = _agent(request, key[0])(key[1], TOOL_CASES[key].args)
    outcome.ok()
    assert_no_leak(repr(outcome.data) + outcome.text, f"{key[0]}.{key[1]}")
    assert xt_world.org_b_snapshot(unscoped) == before


@pytest.mark.parametrize("key", list(TOOL_CASES), ids=_TOOL_IDS)
def test_an_org_id_argument_naming_org_b_is_refused(
    request: pytest.FixtureRequest, xt_world: World, unscoped: Unscoped, key: tuple[str, str]
) -> None:
    _prepare(xt_world, key)
    before = xt_world.org_b_snapshot(unscoped)
    outcome = _agent(request, key[0])(key[1], {**TOOL_CASES[key].args, "org_id": ORG_B})
    assert outcome.error, outcome.data
    assert "org_id" in outcome.text
    assert_no_leak(outcome.text, f"{key[0]}.{key[1]} refusing org_id")
    assert xt_world.org_b_snapshot(unscoped) == before
