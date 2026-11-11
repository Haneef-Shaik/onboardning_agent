"""The hub pipeline per call (PRD §7.4): verify → manifest → schema → tenant → credentials → call →
PII scan → audit → span. Every denial is an audit record and an `outcome=denied` span."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent
from onb_agents.registry import ManifestError
from onb_agents.tokens import AgentClaims, issue_agent_token
from onb_data import ActorType, AuditOutcome, tenant_scope
from onb_data.audit import AuditQuery
from onb_data.testing import InMemoryAuditLog
from onb_mcp_hub.catalog import mongo_catalog
from onb_mcp_hub.gateway import SPAN_PREFIX, Gateway
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry.sdk.trace import ReadableSpan

from .agents import claims, forged, registered
from .fakes import TOKEN_KEY, FakeDownstream, FakeRegistry, Sent, ok

FIND = {"collection": "run_tasks", "filter": {"status": "open"}, "limit": 10}


class Hub:
    def __init__(self, downstream: FakeDownstream | None = None, **agents: Any) -> None:
        self.downstream = downstream or FakeDownstream()
        self.registry = FakeRegistry({"db_agent": registered("db_agent"), **agents})
        self.audit = InMemoryAuditLog()
        self.gateway = Gateway(
            catalog=mongo_catalog(),
            registry=self.registry,
            auditor=self.audit,
            downstream=self.downstream,
            token_key=TOKEN_KEY,
        )

    def call(self, name: str, args: Mapping[str, Any], token: str | None) -> CallToolResult:
        header = None if token is None else f"Bearer {token}"
        return asyncio.run(self.gateway.call(name, dict(args), header))

    def records(self, org: str = "org_acme") -> list[Any]:
        async def page() -> list[Any]:
            with tenant_scope(org):
                return list((await self.audit.list(AuditQuery(), page=1, limit=100)).entries)

        return asyncio.run(page())


def token(c: AgentClaims | None = None, **kw: Any) -> str:
    return issue_agent_token(c or claims(), TOKEN_KEY, **kw)


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def hub_spans(telemetry: CapturedTelemetry) -> list[ReadableSpan]:
    return [s for s in telemetry.spans() if s.name.startswith(SPAN_PREFIX)]


def only_span(telemetry: CapturedTelemetry) -> Mapping[str, Any]:
    spans = hub_spans(telemetry)
    assert len(spans) == 1
    return dict(spans[0].attributes or {})


def assert_denied(hub: Hub, telemetry: CapturedTelemetry, result: CallToolResult, reason: str) -> None:
    assert result.isError is True
    assert f"denied ({reason})" in text(result)
    assert hub.downstream.sent == []
    records = hub.records()
    assert len(records) == 1
    record = records[0]
    assert (record.actor.type, record.actor.id) == (ActorType.AGENT, "db_agent")
    assert record.action == "mcp.tool_call"
    assert record.outcome is AuditOutcome.DENIED
    assert record.reason == reason
    attrs = only_span(telemetry)
    assert attrs["outcome"] == "denied"
    assert attrs["denial.reason"] == reason
    assert attrs["org_id"] == "org_acme"
    assert attrs["agent.name"] == "db_agent"
    assert attrs["run_id"] == "run_1"


# --- box 1: the four named denials ----------------------------------------------------------------


def test_an_expired_token_is_denied_with_an_audit_record_and_a_denied_span(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    expired = token(lifetime_s=60, now=int(time.time()) - 900)
    result = hub.call("mongo__find", FIND, expired)
    assert_denied(hub, telemetry, result, "token_expired")
    assert hub.records()[0].target == "tool:mongo.find/run_tasks"


def test_a_tool_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span(
    telemetry: CapturedTelemetry,
) -> None:
    hub = Hub()
    t = token(claims(scopes=("mongo__find", "mongo__insert_one")))
    result = hub.call("mongo__insert_one", {"collection": "run_tasks", "document": {"x": 1}}, t)
    assert_denied(hub, telemetry, result, "tool_not_in_manifest")
    assert hub.records()[0].target == "tool:mongo.insert_one/run_tasks"


def test_a_collection_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span(
    telemetry: CapturedTelemetry,
) -> None:
    hub = Hub()
    result = hub.call("mongo__find", {"collection": "approvals", "filter": {}, "limit": 5}, token())
    assert_denied(hub, telemetry, result, "collection_not_in_manifest")
    assert hub.records()[0].target == "tool:mongo.find/approvals"


def test_a_vault_collection_under_a_forged_manifest_is_denied_with_an_audit_record_and_a_denied_span(
    telemetry: CapturedTelemetry,
) -> None:
    hub = Hub(db_agent=forged("db_agent", read=("pii_vault", "run_tasks"), tools=("find",)))
    t = token(claims(scopes=("mongo__find",)))
    result = hub.call("mongo__find", {"collection": "pii_vault", "filter": {}, "limit": 5}, t)
    assert_denied(hub, telemetry, result, "vault_denied")
    assert hub.records()[0].target == "tool:mongo.find/pii_vault"


# --- other denials --------------------------------------------------------------------------------


def test_a_forged_token_is_denied_with_a_span_but_no_audit_in_any_org(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    forged_token = issue_agent_token(claims(), "an-attacker-key-0123456789abcdefghij")
    result = hub.call("mongo__find", FIND, forged_token)
    assert result.isError is True
    assert "denied (token_invalid)" in text(result)
    assert hub.records("org_acme") == []  # its org claim is unverified: nothing is written in that org
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["denial.reason"]) == ("denied", "token_invalid")
    assert "org_id" not in attrs


def test_a_missing_token_is_denied(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    result = hub.call("mongo__find", FIND, None)
    assert "denied (no_token)" in text(result)
    assert only_span(telemetry)["denial.reason"] == "no_token"


def test_an_unknown_tool_is_denied_and_audited_without_echoing_its_name(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    result = hub.call("mongo__drop_everything", {}, token())
    assert "denied (unknown_tool)" in text(result)
    assert hub.records()[0].target == "tool:unknown"
    assert only_span(telemetry)["tool.name"] == "unknown"


def test_a_tampered_registry_record_is_denied(telemetry: CapturedTelemetry) -> None:
    hub = Hub(db_agent=ManifestError("the stored record of db_agent does not match its manifest hash"))
    result = hub.call("mongo__find", FIND, token())
    assert_denied(hub, telemetry, result, "manifest_invalid")


def test_a_downstream_refusal_is_audited_as_a_denial(telemetry: CapturedTelemetry) -> None:
    refusal = CallToolResult(content=[TextContent(type="text", text="MongoDB rejected the query")], isError=True)
    hub = Hub(FakeDownstream(reply=lambda sent: refusal))
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is True
    assert hub.records()[0].outcome is AuditOutcome.DENIED
    assert hub.records()[0].reason == "downstream_denied"
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["denial.reason"]) == ("denied", "downstream_denied")


def test_an_unreachable_downstream_is_a_failed_call_without_its_message(telemetry: CapturedTelemetry) -> None:
    hub = Hub(FakeDownstream(fail=ConnectionError("mongodb://user:hunter2@db")))
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is True
    assert "hunter2" not in text(result)
    assert hub.records()[0].outcome is AuditOutcome.FAILED
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["error.type"]) == ("error", "ConnectionError")


def test_an_approval_rule_is_surfaced_to_the_caller(telemetry: CapturedTelemetry) -> None:
    from .agents import with_approval

    hub = Hub(db_agent=with_approval("db_agent", ("update_one:run_tasks",)))
    args = {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"status": "done"}}}
    result = hub.call("mongo__update_one", args, token())
    assert_denied(hub, telemetry, result, "approval_required")
    assert "approval required: update_one:run_tasks" in text(result)


# --- box 2: tenant scope from the token -----------------------------------------------------------


def test_an_allowed_call_is_scoped_to_the_token_org_even_when_its_args_name_another_org(
    telemetry: CapturedTelemetry,
) -> None:
    hub = Hub(FakeDownstream(reply=lambda sent: ok({"documents": [{"run_id": "r1"}], "count": 1})))
    args = {
        "collection": "run_tasks",
        "filter": {"$or": [{"tenant": "org_globex"}, {"owner_org": "org_globex"}]},
        "limit": 5,
    }
    result = hub.call("mongo__find", args, token(claims(org="org_acme")))
    assert result.isError is False
    [sent] = hub.downstream.sent
    assert sent.context.org_id == "org_acme"
    assert sent.args == args  # forwarded as given: the scope travels only in the signed context
    assert sent.context.agent == "db_agent"
    assert sent.context.run_id == "run_1"
    assert sent.context.tool == "find"
    assert sent.context.read == frozenset({"new_hires", "onboarding_runs", "run_tasks", "documents_meta"})
    assert sent.context.write == frozenset({"run_tasks", "documents_meta"})
    [record] = hub.records("org_acme")
    assert (record.outcome, record.target) == (AuditOutcome.SUCCESS, "tool:mongo.find/run_tasks")
    assert hub.records("org_globex") == []
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["org_id"], attrs["result.count"]) == ("ok", "org_acme", 1)


def test_an_org_id_named_in_the_arguments_is_denied_not_honoured(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    args = {"collection": "run_tasks", "filter": {"org_id": "org_globex"}, "limit": 5}
    result = hub.call("mongo__find", args, token())
    assert_denied(hub, telemetry, result, "org_id_not_allowed")


# --- box 3: PII in a downstream response ----------------------------------------------------------


def test_a_raw_mobile_in_a_downstream_response_is_redacted_before_it_reaches_the_agent(
    telemetry: CapturedTelemetry,
) -> None:
    leaked = {"documents": [{"name_token": "ptk_x", "note": "mobile 9876543210, call after 5"}], "count": 1}
    hub = Hub(FakeDownstream(reply=lambda sent: ok(leaked)))
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is False
    body = text(result) + json.dumps(result.structuredContent)
    assert "9876543210" not in body
    assert "[MOBILE]" in body
    attrs = only_span(telemetry)
    assert attrs["pii.redacted"] == 1
    assert "9876543210" not in json.dumps(attrs)
    assert hub.records()[0].reason == "pii_redacted"


# --- box 4: credentials --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["downstream", "context", "token_key"])
def test_a_downstream_credential_in_a_response_never_reaches_the_agent(
    telemetry: CapturedTelemetry, which: str
) -> None:
    downstream = FakeDownstream()

    def echo(sent: Sent) -> CallToolResult:
        leaked = {
            "downstream": downstream.credential,
            "context": f"signed-context-for-{sent.context.tool}-{downstream.credential}",
            "token_key": TOKEN_KEY,
        }[which]
        return ok({"documents": [{"note": f"auth={leaked}"}], "count": 1})

    downstream.reply = echo
    hub = Hub(downstream)
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is True
    assert "denied (credential_in_response)" in text(result)
    assert downstream.credential not in text(result)
    assert TOKEN_KEY not in text(result)
    assert hub.records()[0].reason == "credential_in_response"
    assert only_span(telemetry)["denial.reason"] == "credential_in_response"


def test_no_denial_or_error_message_carries_a_credential(telemetry: CapturedTelemetry) -> None:
    downstream = FakeDownstream(fail=RuntimeError("boom"))
    downstream.fail = RuntimeError(f"connect failed with {downstream.credential}")
    hub = Hub(downstream)
    result = hub.call("mongo__find", FIND, token())
    assert downstream.credential not in text(result)
    assert all(downstream.credential not in json.dumps(dict(s.attributes or {})) for s in telemetry.spans())


# --- audit is fail-closed ------------------------------------------------------------------------


class BrokenAudit(InMemoryAuditLog):
    async def audit(self, **kwargs: Any) -> str:
        raise ConnectionError("audit store down")


def test_an_allowed_call_whose_audit_fails_returns_no_data(telemetry: CapturedTelemetry) -> None:
    hub = Hub(FakeDownstream(reply=lambda sent: ok({"documents": [{"secret": "data"}], "count": 1})))
    hub.gateway = Gateway(
        catalog=mongo_catalog(),
        registry=hub.registry,
        auditor=BrokenAudit(),
        downstream=hub.downstream,
        token_key=TOKEN_KEY,
    )
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is True
    assert "data" not in json.dumps(result.structuredContent)
    assert "internal error" in text(result)
    assert only_span(telemetry)["outcome"] == "error"


def test_a_denial_whose_audit_fails_is_still_denied(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    hub.gateway = Gateway(
        catalog=mongo_catalog(),
        registry=hub.registry,
        auditor=BrokenAudit(),
        downstream=hub.downstream,
        token_key=TOKEN_KEY,
    )
    result = hub.call("mongo__find", {"collection": "approvals", "filter": {}, "limit": 1}, token())
    assert "denied (collection_not_in_manifest)" in text(result)
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["audit.failed"]) == ("denied", True)
