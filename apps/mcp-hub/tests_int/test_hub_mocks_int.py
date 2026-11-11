"""MOCK-1 against the real stack: the hub (real registry, real `AuditLog` on testcontainers MongoDB) in
front of the real mcp-mocks server on loopback.

Each test uses its own run id and its own idempotency key (`task_id`), so its span, its audit records
and its mock records are its own. The records are read back from `audit_log` by the span's trace id.
"""

from __future__ import annotations

import secrets
from typing import Any

from onb_data import ActorType, AuditOutcome

from .client import agent_claims, call, everything, new_run, text, trail
from .conftest import Stack

PROVISIONING_SCOPES = ("mongo__find", "mocks__create_bitbucket_account", "mocks__provision_laptop")
HIRE = "65f0aaaabbbbccccddddeeee"


def _task() -> str:
    return secrets.token_hex(12)  # a fresh 24-hex task id: this test's own idempotency key


def _bitbucket(task_id: str) -> dict[str, Any]:
    return {"task_id": task_id, "hire_id": HIRE, "workspace": "acme-eng", "groups": ["backend"]}


def _laptop(task_id: str) -> dict[str, Any]:
    return {"task_id": task_id, "hire_id": HIRE, "profile": "engineering", "office": "bangalore"}


def _mock_records(stack: Stack, org: str = "org_acme") -> int:
    return stack.mocks_store.count(org)


# --- box 1 -----------------------------------------------------------------------------------------


def test_int_the_provisioning_agent_token_creates_a_bitbucket_account_through_the_hub(stack: Stack) -> None:
    run_id, task_id = new_run(), _task()
    before = _mock_records(stack)
    claims = agent_claims("provisioning_agent", "org_acme", run_id, PROVISIONING_SCOPES)
    result = call(stack, claims, "mocks__create_bitbucket_account", _bitbucket(task_id))
    assert result.isError is False, text(result)
    assert result.structuredContent is not None
    assert (result.structuredContent["status"], result.structuredContent["hire_id"]) == ("active", HIRE)
    assert _mock_records(stack) == before + 1
    found = trail(stack, run_id, "org_acme")
    assert (found.attrs["outcome"], found.attrs["agent.name"], found.attrs["mcp.server"]) == (
        "ok",
        "provisioning_agent",
        "mocks",
    )
    [record] = found.records
    assert (record.actor.type, record.actor.id) == (ActorType.AGENT, "provisioning_agent")
    assert (record.action, record.target, record.outcome) == (
        "mcp.tool_call",
        "tool:mocks.create_bitbucket_account",
        AuditOutcome.SUCCESS,
    )
    mock_spans = [s for s in stack.telemetry.spans() if s.name == "mcp.mocks.create_bitbucket_account"]
    assert any(dict(s.attributes or {}).get("run_id") == run_id for s in mock_spans)


def test_int_the_provisioning_agent_token_orders_a_laptop_through_the_hub(stack: Stack) -> None:
    run_id = new_run()
    claims = agent_claims("provisioning_agent", "org_acme", run_id, PROVISIONING_SCOPES)
    result = call(stack, claims, "mocks__provision_laptop", _laptop(_task()))
    assert result.isError is False, text(result)
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "ordered"
    [record] = trail(stack, run_id, "org_acme").records
    assert (record.target, record.outcome) == ("tool:mocks.provision_laptop", AuditOutcome.SUCCESS)


def test_int_no_downstream_credential_reaches_the_provisioning_agent(stack: Stack) -> None:
    claims = agent_claims("provisioning_agent", "org_acme", new_run(), PROVISIONING_SCOPES)
    returned = everything(call(stack, claims, "mocks__provision_laptop", _laptop(_task())))
    for secret in (stack.mocks_key, stack.context_key, stack.token_key):
        assert secret not in returned


# --- box 2 -----------------------------------------------------------------------------------------


def test_int_replaying_with_the_same_idempotency_key_returns_the_same_result_and_creates_nothing_new(
    stack: Stack,
) -> None:
    task_id = _task()
    first_run, replay_run = new_run(), new_run()
    created_before = stack.mocks_store.creations
    first = call(
        stack,
        agent_claims("provisioning_agent", "org_acme", first_run, PROVISIONING_SCOPES),
        "mocks__create_bitbucket_account",
        _bitbucket(task_id),
    )
    after_first = _mock_records(stack)
    again = call(
        stack,
        agent_claims("provisioning_agent", "org_acme", replay_run, PROVISIONING_SCOPES),
        "mocks__create_bitbucket_account",
        _bitbucket(task_id),
    )
    assert first.isError is False, text(first)
    assert again.isError is False, text(again)
    assert again.structuredContent == first.structuredContent
    assert text(again) == text(first)
    assert _mock_records(stack) == after_first  # nothing new
    assert stack.mocks_store.creations == created_before + 1
    replayed = [
        dict(s.attributes or {}).get("mock.replayed")
        for s in stack.telemetry.spans()
        if s.name == "mcp.mocks.create_bitbucket_account" and dict(s.attributes or {}).get("run_id") == replay_run
    ]
    assert replayed == [True]
    for run_id in (first_run, replay_run):
        [record] = trail(stack, run_id, "org_acme").records
        assert record.outcome is AuditOutcome.SUCCESS


def test_int_the_same_idempotency_key_with_other_arguments_is_refused_and_creates_nothing(stack: Stack) -> None:
    task_id = _task()
    claims = agent_claims("provisioning_agent", "org_acme", new_run(), PROVISIONING_SCOPES)
    call(stack, claims, "mocks__provision_laptop", _laptop(task_id))
    after_first = _mock_records(stack)
    run_id = new_run()
    changed = {**_laptop(task_id), "profile": "design"}
    result = call(
        stack,
        agent_claims("provisioning_agent", "org_acme", run_id, PROVISIONING_SCOPES),
        "mocks__provision_laptop",
        changed,
    )
    assert result.isError is True
    assert _mock_records(stack) == after_first
    [record] = trail(stack, run_id, "org_acme").records
    assert (record.outcome, record.reason) == (AuditOutcome.DENIED, "downstream_denied")


# --- box 3 -----------------------------------------------------------------------------------------


def _assert_denied(stack: Stack, run_id: str, agent: str, reason: str, before: int) -> None:
    found = trail(stack, run_id, "org_acme")
    assert (found.attrs["outcome"], found.attrs["denial.reason"]) == ("denied", reason)
    [record] = found.records
    assert (record.actor.id, record.outcome, record.reason) == (agent, AuditOutcome.DENIED, reason)
    assert record.target == "tool:mocks.create_bitbucket_account"
    assert _mock_records(stack) == before  # never forwarded
    assert not [
        s
        for s in stack.telemetry.spans()
        if s.name.startswith("mcp.mocks.") and dict(s.attributes or {}).get("run_id") == run_id
    ]


def test_int_an_agent_without_the_mock_tool_in_its_manifest_is_denied(stack: Stack) -> None:
    """db_agent's registered manifest grants no mock tools; a token scoped to one does not help."""
    run_id, before = new_run(), _mock_records(stack)
    claims = agent_claims("db_agent", "org_acme", run_id, ("mongo__find", "mocks__create_bitbucket_account"))
    result = call(stack, claims, "mocks__create_bitbucket_account", _bitbucket(_task()))
    assert result.isError is True
    assert "denied (server_not_in_manifest)" in text(result)
    _assert_denied(stack, run_id, "db_agent", "server_not_in_manifest", before)


def test_int_every_registered_agent_without_the_mock_tool_is_denied(stack: Stack) -> None:
    """Each config/agents manifest other than the provisioning agent's lacks the mock tools."""
    for agent in ("approvals_agent", "run_assistant", "template_builder", "templates_agent"):
        run_id, before = new_run(), _mock_records(stack)
        claims = agent_claims(agent, "org_acme", run_id, ("mocks__create_bitbucket_account",))
        result = call(stack, claims, "mocks__create_bitbucket_account", _bitbucket(_task()))
        assert "denied (server_not_in_manifest)" in text(result), agent
        _assert_denied(stack, run_id, agent, "server_not_in_manifest", before)


def test_int_a_provisioning_token_without_the_scope_is_denied(stack: Stack) -> None:
    run_id, before = new_run(), _mock_records(stack)
    claims = agent_claims("provisioning_agent", "org_acme", run_id, ("mocks__provision_laptop",))
    result = call(stack, claims, "mocks__create_bitbucket_account", _bitbucket(_task()))
    assert "denied (scope_not_granted)" in text(result)
    _assert_denied(stack, run_id, "provisioning_agent", "scope_not_granted", before)
