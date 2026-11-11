"""Fixes from the HUB-1 code and security reviews, each pinned by a test."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent
from onb_data import AuditOutcome
from onb_mcp_hub.catalog import hub_name, mongo_catalog
from onb_mcp_hub.policy import CallDeniedError, authorize
from onb_mcp_hub.scan import scan_result
from onb_telemetry.testing import CapturedTelemetry

from .agents import claims, registered
from .fakes import FakeDownstream, FakeRegistry, Sent, ok
from .test_gateway import FIND, Hub, only_span, text, token

CATALOG = mongo_catalog()


class ExplodingRegistry(FakeRegistry):
    async def get_agent(self, name: str) -> Any:
        raise RuntimeError("mongodb://hub:S3cretPassw0rd@db:27017 is down")


def test_a_registry_failure_is_audited_traced_and_never_echoed(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    hub.gateway._registry = ExplodingRegistry()
    result = hub.call("mongo__find", FIND, token())
    assert result.isError is True
    assert "S3cret" not in text(result)
    assert "internal error" in text(result)
    [record] = hub.records()
    assert (record.outcome, record.reason) == (AuditOutcome.FAILED, "internal_error")
    attrs = only_span(telemetry)
    assert (attrs["outcome"], attrs["error.reason"]) == ("error", "internal_error")


def test_a_withheld_write_is_audited_as_failed_because_it_may_have_landed(telemetry: CapturedTelemetry) -> None:
    downstream = FakeDownstream()
    downstream.reply = lambda sent: ok({"inserted_id": downstream.credential, "count": 1})
    hub = Hub(downstream)
    args = {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"y": 1}}}
    result = hub.call("mongo__update_one", args, token())
    assert "denied (credential_in_response)" in text(result)
    [record] = hub.records()
    assert (record.outcome, record.reason) == (AuditOutcome.FAILED, "credential_in_response")
    assert only_span(telemetry)["outcome"] == "denied"


def test_a_downstream_internal_error_is_a_failure_not_a_denial(telemetry: CapturedTelemetry) -> None:
    broken = CallToolResult(content=[TextContent(type="text", text="find: internal error")], isError=True)
    hub = Hub(FakeDownstream(reply=lambda sent: broken))
    hub.call("mongo__find", FIND, token())
    [record] = hub.records()
    assert (record.outcome, record.reason) == (AuditOutcome.FAILED, "downstream_error")
    assert only_span(telemetry)["outcome"] == "error"


def test_an_echoed_call_context_is_withheld_even_though_it_shares_nothing_with_the_key(
    telemetry: CapturedTelemetry,
) -> None:
    downstream = FakeDownstream()

    def echo(sent: Sent) -> CallToolResult:
        assert downstream.credential not in sent.signed
        return ok({"documents": [{"note": sent.signed}], "count": 1})

    downstream.reply = echo
    hub = Hub(downstream)
    result = hub.call("mongo__find", FIND, token())
    assert "denied (credential_in_response)" in text(result)


@pytest.mark.parametrize("collection", ["Mobile9876543210", "x" * 60])
def test_agent_text_in_a_collection_name_never_reaches_the_audit_or_the_span(
    telemetry: CapturedTelemetry, collection: str
) -> None:
    hub = Hub()
    hub.call("mongo__find", {"collection": collection, "filter": {}, "limit": 1}, token())
    [record] = hub.records()
    assert record.target == "tool:mongo.find/other"
    assert only_span(telemetry)["db.collection"] == "other"


def test_a_vault_attempt_stays_visible_in_the_audit_target(telemetry: CapturedTelemetry) -> None:
    hub = Hub()
    hub.call("mongo__find", {"collection": "pii_vault_aadhaar", "filter": {}, "limit": 1}, token())
    assert hub.records()[0].target == "tool:mongo.find/pii_vault_aadhaar"


def test_an_org_id_rename_target_is_denied_at_the_hub() -> None:
    args = {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$rename": {"owner": "org_id"}}}
    with pytest.raises(CallDeniedError) as caught:
        authorize(claims(), registered("db_agent"), CATALOG[hub_name("mongo", "update_one")], args)
    assert caught.value.reason == "org_id_not_allowed"


@pytest.mark.parametrize(
    "args",
    [
        {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"note": "mobile 9876543210"}}},
        {"collection": "run_tasks", "filter": {"phone": 9876543210}, "update": {"$set": {"y": 1}}},
        {"collection": "run_tasks", "filter": {"x": 1}, "update": {"$set": {"mail": "a.b@example.com"}}},
    ],
    ids=["mobile-text", "mobile-number", "email"],
)
def test_raw_pii_in_arguments_is_denied(args: dict[str, Any]) -> None:
    with pytest.raises(CallDeniedError) as caught:
        authorize(claims(), registered("db_agent"), CATALOG[hub_name("mongo", "update_one")], args)
    assert caught.value.reason == "raw_pii_in_arguments"
    assert "9876543210" not in str(caught.value)


def test_ids_and_vault_tokens_in_arguments_are_not_mistaken_for_pii() -> None:
    args = {
        "collection": "run_tasks",
        "filter": {"_id": {"$oid": "9876543210aa9876543210aa"}, "hire_id": "9876543210aa9876543210aa"},
        "limit": 5,
    }
    tool = CATALOG[hub_name("mongo", "find")]
    assert authorize(claims(), registered("db_agent"), tool, args).collection == "run_tasks"


def test_a_number_holding_a_mobile_is_redacted_from_a_response() -> None:
    scanned = scan_result(ok({"documents": [{"phone": 9876543210, "n": 3}], "count": 1}), secrets=())
    assert scanned.redacted == 1
    assert scanned.result.structuredContent == {"documents": [{"phone": "[MOBILE]", "n": 3}], "count": 1}
    assert "9876543210" not in json.dumps(scanned.result.structuredContent)


def test_a_hex_string_outside_an_id_key_is_scanned() -> None:
    scanned = scan_result(ok({"documents": [{"note": "abc9876543210def0123abcd"}], "count": 1}), secrets=())
    assert scanned.redacted >= 1
    kept = scan_result(ok({"documents": [{"_id": "abc9876543210def0123abcd"}], "count": 1}), secrets=())
    assert kept.redacted == 0
