"""The hub's per-call policy (PRD §7.4): manifest, scopes, schema, collections, vault, approvals."""

from __future__ import annotations

from typing import Any

import pytest
from onb_mcp_hub.catalog import HubTool, hub_name, mongo_catalog
from onb_mcp_hub.policy import FORBIDDEN_COLLECTIONS, CallDeniedError, authorize

from .agents import claims, forged, registered, with_approval

CATALOG = mongo_catalog()


def tool(name: str) -> HubTool:
    return CATALOG[hub_name("mongo", name)]


def denial(call: Any) -> CallDeniedError:
    with pytest.raises(CallDeniedError) as caught:
        call()
    return caught.value


FIND = {"collection": "run_tasks", "filter": {"status": "open"}, "limit": 10}


def test_the_catalog_serves_the_six_mongo_tools_under_hub_names() -> None:
    assert sorted(CATALOG) == sorted(
        f"mongo__{t}" for t in ("list_collections", "find", "count", "aggregate", "insert_one", "update_one")
    )
    find = tool("find")
    assert (find.server, find.tool, find.read_only) == ("mongo", "find", True)
    assert find.input_schema["additionalProperties"] is False
    assert tool("update_one").read_only is False


def test_an_allowed_call_gets_the_manifest_collections_for_its_context() -> None:
    grant = authorize(claims(), registered("db_agent"), tool("find"), FIND)
    assert grant.read == frozenset({"new_hires", "onboarding_runs", "run_tasks", "documents_meta"})
    assert grant.write == frozenset({"run_tasks", "documents_meta"})
    assert grant.collection == "run_tasks"


def test_an_unknown_tool_is_denied() -> None:
    assert denial(lambda: authorize(claims(), registered("db_agent"), None, {})).reason == "unknown_tool"


def test_an_unknown_or_retired_agent_is_denied() -> None:
    assert denial(lambda: authorize(claims(), None, tool("find"), FIND)).reason == "unknown_agent"
    retired = registered("db_agent", active=False)
    assert denial(lambda: authorize(claims(), retired, tool("find"), FIND)).reason == "agent_retired"


def test_a_token_for_another_agent_than_the_record_is_denied() -> None:
    assert denial(lambda: authorize(claims("db_agent"), registered("templates_agent"), tool("find"), FIND)).reason == (
        "unknown_agent"
    )


def test_a_tool_outside_the_manifest_is_denied() -> None:
    scopes = ("mongo__find", "mongo__insert_one")
    found = denial(
        lambda: authorize(
            claims(scopes=scopes),
            registered("db_agent"),
            tool("insert_one"),
            {"collection": "run_tasks", "document": {"x": 1}},
        )
    )
    assert found.reason == "tool_not_in_manifest"


def test_a_server_outside_the_manifest_is_denied() -> None:
    other = HubTool("mocks", "provision_laptop", "mocks__provision_laptop", "", {"type": "object"}, None, False)
    found = denial(lambda: authorize(claims(scopes=("mocks__provision_laptop",)), registered("db_agent"), other, {}))
    assert found.reason == "server_not_in_manifest"


def test_a_tool_outside_the_token_scopes_is_denied() -> None:
    found = denial(lambda: authorize(claims(scopes=("mongo__count",)), registered("db_agent"), tool("find"), FIND))
    assert found.reason == "scope_not_granted"


def test_a_collection_outside_the_manifest_is_denied() -> None:
    args = {"collection": "approvals", "filter": {}, "limit": 5}
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("find"), args))
    assert found.reason == "collection_not_in_manifest"


def test_a_write_to_a_read_only_collection_is_denied() -> None:
    args = {"collection": "new_hires", "filter": {"x": 1}, "update": {"$set": {"y": 2}}}
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("update_one"), args))
    assert found.reason == "collection_not_in_manifest"


@pytest.mark.parametrize("collection", sorted(FORBIDDEN_COLLECTIONS))
def test_a_vault_collection_under_a_forged_manifest_is_hard_denied(collection: str) -> None:
    agent = forged("db_agent", read=(collection, "run_tasks"), tools=("find",))
    args = {"collection": collection, "filter": {}, "limit": 1}
    found = denial(lambda: authorize(claims(scopes=("mongo__find",)), agent, tool("find"), args))
    assert found.reason == "vault_denied"


def test_a_vault_name_anywhere_in_the_arguments_is_hard_denied() -> None:
    agent = forged("db_agent", read=("pii_vault_aadhaar", "run_tasks"), tools=("aggregate",))
    args = {"collection": "run_tasks", "pipeline": [{"$lookup": {"from": "pii_vault_aadhaar", "as": "v"}}]}
    found = denial(lambda: authorize(claims(scopes=("mongo__aggregate",)), agent, tool("aggregate"), args))
    assert found.reason == "vault_denied"


def test_a_forged_manifest_never_puts_the_vault_into_the_call_context() -> None:
    agent = forged("db_agent", read=("pii_vault", "org_keys", "run_tasks"), tools=("list_collections",))
    grant = authorize(claims(scopes=("mongo__list_collections",)), agent, tool("list_collections"), {})
    assert grant.read == frozenset({"run_tasks"})


def test_a_collection_no_mcp_database_user_holds_is_denied_even_if_the_manifest_lists_it() -> None:
    agent = forged("db_agent", read=("events", "run_tasks"), tools=("find",))
    args = {"collection": "events", "filter": {}, "limit": 1}
    found = denial(lambda: authorize(claims(scopes=("mongo__find",)), agent, tool("find"), args))
    assert found.reason == "collection_not_provisioned"


@pytest.mark.parametrize(
    "args",
    [
        {"collection": "run_tasks", "filter": {}},  # no limit
        {"collection": "run_tasks", "filter": {}, "limit": 101},
        {"collection": "run_tasks", "filter": {}, "limit": 5, "extra": 1},
        {"filter": {}, "limit": 5},
    ],
    ids=["no-limit", "limit-101", "extra", "no-collection"],
)
def test_arguments_off_the_tool_contract_are_denied(args: dict[str, Any]) -> None:
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("find"), args))
    assert found.reason == "invalid_arguments"
    assert "101" not in str(found)


def test_an_org_id_argument_is_denied() -> None:
    args = {"collection": "run_tasks", "filter": {"$or": [{"org_id": "org_globex"}]}, "limit": 5}
    assert denial(lambda: authorize(claims(), registered("db_agent"), tool("find"), args)).reason == (
        "org_id_not_allowed"
    )


def test_the_query_validator_runs_at_the_hub_and_its_reason_is_kept() -> None:
    args = {"collection": "run_tasks", "filter": {"$where": "sleep(1000)"}, "limit": 5}
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("find"), args))
    assert found.reason == "forbidden_operator"
    assert "sleep" not in str(found)


def test_a_pipeline_stage_off_the_allowlist_is_denied() -> None:
    args = {"collection": "run_tasks", "pipeline": [{"$unionWith": "approvals"}]}
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("aggregate"), args))
    assert found.reason in {"stage_not_allowed", "forbidden_operator"}


def test_an_approval_rule_is_surfaced_until_the_token_carries_the_approval() -> None:
    agent = with_approval("db_agent", ("update_one:run_tasks",))
    args = {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"status": "done"}}}
    found = denial(lambda: authorize(claims(), agent, tool("update_one"), args))
    assert found.reason == "approval_required"
    assert "update_one:run_tasks" in str(found)
    approved = claims(scopes=(*claims().scopes, "approved:update_one:run_tasks"))
    assert authorize(approved, agent, tool("update_one"), args).collection == "run_tasks"
    other = {**args, "collection": "documents_meta"}
    assert authorize(claims(), agent, tool("update_one"), other).collection == "documents_meta"


def test_a_tool_level_approval_rule_applies_to_every_collection() -> None:
    agent = with_approval("db_agent", ("update_one",))
    args = {"collection": "documents_meta", "filter": {"x": 1}, "update": {"$set": {"y": 1}}}
    assert denial(lambda: authorize(claims(), agent, tool("update_one"), args)).reason == "approval_required"


def test_denial_messages_never_echo_argument_values() -> None:
    args = {"collection": "approvals", "filter": {"note": "secret-marker"}, "limit": 5}
    found = denial(lambda: authorize(claims(), registered("db_agent"), tool("find"), args))
    assert "secret-marker" not in str(found)
