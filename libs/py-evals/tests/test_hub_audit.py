"""The runner reads the MCP hub's audit records (HUB-1, ADR 0006) as well as the stand-in's."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from onb_evals.capture import AuditEntry, ToolOutcome
from onb_evals.toolbox import AUDIT_QUERIES, audit_from_documents

AT = datetime(2026, 11, 11, tzinfo=UTC)


def hub_record(target: str, outcome: str, reason: str | None = None, org: str = "org_acme") -> dict[str, Any]:
    """A record as `AuditLog.audit(...)` stores it for a hub tool call."""
    return {
        "_id": "a" * 32,
        "org_id": org,
        "actor": {"type": "agent", "id": "db_agent"},
        "action": "mcp.tool_call",
        "target": target,
        "outcome": outcome,
        "reason": reason,
        "trace_id": None,
        "at": AT,
    }


def test_hub_records_become_audit_entries_with_the_tool_and_outcome() -> None:
    entries = audit_from_documents(
        [
            hub_record("tool:mongo.find/run_tasks", "success"),
            hub_record("tool:mongo.find/pii_vault", "denied", "vault_denied"),
            hub_record("tool:mongo.count", "failed", "downstream_unavailable"),
            hub_record("tool:unknown", "denied", "unknown_tool"),
        ]
    )
    assert entries == (
        AuditEntry("org_acme", "find", {}, ToolOutcome.ALLOWED, ()),
        AuditEntry("org_acme", "find", {}, ToolOutcome.DENIED, ()),
        AuditEntry("org_acme", "count", {}, ToolOutcome.ERROR, ()),
        AuditEntry("org_acme", "unknown", {}, ToolOutcome.DENIED, ()),
    )


def test_other_audit_records_are_not_tool_calls() -> None:
    other = {**hub_record("hire:h_1/pan", "success"), "action": "pii.reveal", "actor": {"type": "user", "id": "u_1"}}
    assert audit_from_documents([other]) == ()


def test_stand_in_records_still_read_back() -> None:
    doc = {"kind": "mcp_call", "org_id": "org_acme", "tool": "find", "args_json": '{"a": 1}', "outcome": "allowed"}
    assert audit_from_documents([doc]) == (AuditEntry("org_acme", "find", {"a": 1}, ToolOutcome.ALLOWED, ()),)


def test_the_runner_query_selects_both_shapes() -> None:
    assert AUDIT_QUERIES == ({"kind": "mcp_call"}, {"action": "mcp.tool_call"})


def test_an_unexpected_hub_outcome_reads_as_an_error_not_a_crash() -> None:
    [entry] = audit_from_documents([hub_record("tool:mongo.find", "something_new")])
    assert entry.outcome is ToolOutcome.ERROR


def test_the_runner_reads_both_shapes_back_in_call_order() -> None:
    from datetime import timedelta

    from onb_evals.runner import _call_time

    later = {**hub_record("tool:mongo.count", "success"), "at": AT + timedelta(seconds=2)}
    earlier = {"kind": "mcp_call", "org_id": "org_acme", "tool": "find", "outcome": "allowed", "at": AT}
    assert sorted([later, earlier], key=_call_time) == [earlier, later]
    assert _call_time({"kind": "mcp_call"}) < AT
