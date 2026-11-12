"""The hub against the real stack (testcontainers MongoDB, real mcp-mongo, real registry and audit log).

Each test uses its own run id, so its span and the audit records under that span's trace are its own.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from onb_agents.tokens import hub_client_config, issue_agent_token
from onb_data import ActorType, AuditOutcome

from .client import agent_claims, call, everything, list_tools, new_marker, new_run, text, trail
from .conftest import Stack

Unscoped = Callable[[str], AbstractContextManager[None]]
DB_SCOPES = ("mongo__find", "mongo__count", "mongo__aggregate", "mongo__update_one")


def assert_denied_trail(stack: Stack, run_id: str, reason: str, *, agent: str = "db_agent") -> None:
    found = trail(stack, run_id, "org_acme")
    assert (found.attrs["outcome"], found.attrs["denial.reason"]) == ("denied", reason)
    assert found.attrs["org_id"] == "org_acme"
    [record] = found.records
    assert (record.actor.type, record.actor.id) == (ActorType.AGENT, agent)
    assert (record.action, record.outcome, record.reason) == ("mcp.tool_call", AuditOutcome.DENIED, reason)
    assert record.org_id == "org_acme"


# --- box 1 -----------------------------------------------------------------------------------------


def test_int_an_expired_token_is_denied_with_an_audit_record_and_a_denied_span(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    args = {"collection": "run_tasks", "filter": {}, "limit": 5}
    result = call(stack, claims, "mongo__find", args, lifetime_s=60, now=int(time.time()) - 900)
    assert result.isError is True
    assert "denied (token_expired)" in text(result)
    assert_denied_trail(stack, run_id, "token_expired")


def test_int_a_tool_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, (*DB_SCOPES, "mongo__insert_one"))
    result = call(stack, claims, "mongo__insert_one", {"collection": "run_tasks", "document": {"x": 1}})
    assert "denied (tool_not_in_manifest)" in text(result)
    assert_denied_trail(stack, run_id, "tool_not_in_manifest")


def test_int_a_collection_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    result = call(stack, claims, "mongo__find", {"collection": "approvals", "filter": {}, "limit": 5})
    assert "denied (collection_not_in_manifest)" in text(result)
    assert_denied_trail(stack, run_id, "collection_not_in_manifest")


def _forge_registry_record(root_db: Any, name: str, read: list[str]) -> None:
    """Rewrite an agent's registry record behind the registry's back, hash recomputed to match."""
    record = root_db["agent_registry"].find_one({"_id": "db_agent"})
    manifest = copy.deepcopy(record["manifest"])
    manifest["name"] = name
    manifest["mcp"]["mongo"]["collections"]["read"] = read
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    root_db["agent_registry"].replace_one(
        {"_id": name}, {**record, "_id": name, "manifest": manifest, "sha256": digest}, upsert=True
    )


def test_int_a_vault_collection_under_a_forged_manifest_is_denied_with_an_audit_record_and_a_denied_span(
    stack: Stack, root_db: Any
) -> None:
    _forge_registry_record(root_db, "forged_agent", ["pii_vault", "run_tasks"])
    run_id = new_run()
    claims = agent_claims("forged_agent", "org_acme", run_id, ("mongo__find",))
    result = call(stack, claims, "mongo__find", {"collection": "pii_vault", "filter": {}, "limit": 5})
    assert result.isError is True
    # The registry re-validates the stored record, so the forged manifest never loads; the hub's own
    # hard deny of the vault (unit test `..._forged_manifest_...`) stands behind it.
    found = trail(stack, run_id, "org_acme")
    assert found.attrs["outcome"] == "denied"
    assert found.attrs["denial.reason"] == "manifest_invalid"
    [record] = found.records
    assert (record.outcome, record.target) == (AuditOutcome.DENIED, "tool:mongo.find/pii_vault")
    assert result.structuredContent is None  # nothing from the collection came back


def test_int_a_vault_name_in_a_pipeline_is_hard_denied_for_a_registered_agent(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    pipeline = [{"$lookup": {"from": "pii_vault", "localField": "a", "foreignField": "b", "as": "v"}}]
    result = call(stack, claims, "mongo__aggregate", {"collection": "run_tasks", "pipeline": pipeline})
    assert "denied (vault_denied)" in text(result)
    assert_denied_trail(stack, run_id, "vault_denied")


# --- box 2 -----------------------------------------------------------------------------------------


def test_int_an_allowed_call_is_scoped_to_the_token_org_even_when_its_args_name_another_org(
    stack: Stack, root_db: Any
) -> None:
    marker = new_marker()
    root_db["run_tasks"].insert_many(
        [
            {"org_id": "org_acme", "marker": marker, "owner": "org_globex", "status": "open"},
            {"org_id": "org_globex", "marker": marker, "owner": "org_globex", "status": "open"},
            {"org_id": "org_globex", "marker": marker, "owner": "org_globex", "status": "open"},
        ]
    )
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    args = {"collection": "run_tasks", "filter": {"marker": marker, "owner": "org_globex"}, "limit": 10}
    result = call(stack, claims, "mongo__find", args)
    assert result.isError is False, text(result)
    documents = result.structuredContent["documents"]  # type: ignore[index]
    assert len(documents) == 1
    assert {d.get("org_id", "org_acme") for d in documents} == {"org_acme"}
    count = call(
        stack,
        agent_claims("db_agent", "org_acme", new_run(), DB_SCOPES),
        "mongo__count",
        {"collection": "run_tasks", "filter": {"marker": marker}},
    )
    assert count.structuredContent == {"count": 1}
    found = trail(stack, run_id, "org_acme")
    assert (found.attrs["outcome"], found.attrs["org_id"]) == ("ok", "org_acme")
    assert [r.outcome for r in found.records] == [AuditOutcome.SUCCESS]
    assert trail(stack, run_id, "org_globex").records == ()


def test_int_an_update_naming_another_orgs_document_matches_nothing(stack: Stack, root_db: Any, unscoped: Any) -> None:
    marker = new_marker()
    root_db["run_tasks"].insert_one({"org_id": "org_globex", "marker": marker, "status": "open"})
    claims = agent_claims("db_agent", "org_acme", new_run(), DB_SCOPES)
    args = {"collection": "run_tasks", "filter": {"marker": marker}, "update": {"$set": {"status": "hijacked"}}}
    result = call(stack, claims, "mongo__update_one", args)
    assert result.structuredContent == {"matched": 0, "modified": 0, "count": 0}
    assert root_db["run_tasks"].find_one({"org_id": "org_globex", "marker": marker})["status"] == "open"


def test_int_an_org_id_in_the_arguments_is_denied(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    result = call(
        stack, claims, "mongo__find", {"collection": "run_tasks", "filter": {"org_id": "org_globex"}, "limit": 5}
    )
    assert "denied (org_id_not_allowed)" in text(result)
    assert_denied_trail(stack, run_id, "org_id_not_allowed")


# --- box 3 -----------------------------------------------------------------------------------------


def test_int_a_raw_mobile_stored_in_a_tenant_document_is_redacted_before_it_reaches_the_agent(
    stack: Stack, root_db: Any
) -> None:
    marker = new_marker()
    mobile = "9876543210"  # synthetic: a leaked raw value that should only ever be in the vault
    root_db["run_tasks"].insert_one({"org_id": "org_acme", "marker": marker, "note": f"call {mobile} re laptop"})
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    result = call(stack, claims, "mongo__find", {"collection": "run_tasks", "filter": {"marker": marker}, "limit": 5})
    assert result.isError is False
    assert mobile not in everything(result)
    assert result.structuredContent["documents"][0]["note"] == "call [MOBILE] re laptop"  # type: ignore[index]
    found = trail(stack, run_id, "org_acme")
    assert found.attrs["pii.redacted"] == 1
    assert [r.reason for r in found.records] == ["pii_redacted"]
    assert all(mobile not in json.dumps(dict(s.attributes or {})) for s in stack.telemetry.spans())


# --- box 4 -----------------------------------------------------------------------------------------


def test_int_downstream_credentials_never_appear_in_responses_or_agent_side_config(stack: Stack, root_db: Any) -> None:
    marker = new_marker()
    root_db["run_tasks"].insert_one({"org_id": "org_acme", "marker": marker, "note": "plain"})
    claims = agent_claims("db_agent", "org_acme", new_run(), DB_SCOPES)
    token = issue_agent_token(claims, stack.token_key)
    config = json.dumps(hub_client_config(stack.hub_url, token))
    responses = [
        call(stack, claims, "mongo__find", {"collection": "run_tasks", "filter": {"marker": marker}, "limit": 5}),
        call(stack, claims, "mongo__find", {"collection": "approvals", "filter": {}, "limit": 5}),  # a denial
        call(stack, claims, "mongo__count", {"collection": "run_tasks", "filter": {"$where": "1"}}),  # a refusal
    ]
    listed = json.dumps(list_tools(stack, claims))
    for secret in (stack.context_key, stack.mongo_uri, "mcp_mongo_ro", "mcp_mongo_rw"):
        assert secret not in config
        assert secret not in listed
        for response in responses:
            assert secret not in everything(response)


def test_int_a_stored_credential_is_withheld_whole(stack: Stack, root_db: Any) -> None:
    marker = new_marker()
    root_db["run_tasks"].insert_one({"org_id": "org_acme", "marker": marker, "note": f"key {stack.context_key}"})
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    result = call(stack, claims, "mongo__find", {"collection": "run_tasks", "filter": {"marker": marker}, "limit": 5})
    assert "denied (credential_in_response)" in text(result)
    assert stack.context_key not in everything(result)
    assert_denied_trail(stack, run_id, "credential_in_response")


def test_int_the_agent_token_never_reaches_mcp_mongo(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("db_agent", "org_acme", run_id, DB_SCOPES)
    call(stack, claims, "mongo__count", {"collection": "run_tasks", "filter": {}})
    mongo_spans = [
        s
        for s in stack.telemetry.spans()
        if s.name == "mcp.mongo.count" and (s.attributes or {}).get("run_id") == run_id
    ]
    [span] = mongo_spans
    assert (span.attributes or {})["org_id"] == "org_acme"  # mcp-mongo got the org from the hub's context
