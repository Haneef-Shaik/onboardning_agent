"""Agent registry contracts (REG-1; PRD §10 `GET /agents`)."""

import re
from datetime import UTC, datetime

import pytest
from onb_contracts import ok
from onb_contracts.agents import AGENT_NAME_PATTERN, AgentEnvelope, AgentRecord, AgentsEnvelope
from pydantic import ValidationError

RECORD = {
    "name": "db_agent",
    "version": 2,
    "active": True,
    "purpose": "Read and update onboarding records for a single run",
    "llm": {"alias": "standard", "connector": None, "model": None, "params": {}},
    "mcp": {
        "mongo": {"tools": ["find", "update_one"], "collections": {"read": ["run_tasks"], "write": ["run_tasks"]}},
        "mocks": {"tools": ["provision_laptop"], "collections": None},
    },
    "tools": ["date_calc"],
    "memory_scopes": ["run_state:read"],
    "requires_approval": [],
    "limits": {"max_steps": 8, "max_tokens": 20000, "timeout_s": 60},
    "sha256": "c" * 64,
    "updated_at": "2026-11-10T09:30:00Z",
}


def test_a_record_round_trips_with_utc_time() -> None:
    record = AgentRecord.model_validate(RECORD)
    assert record.updated_at == datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
    assert record.model_dump(mode="json") == RECORD


@pytest.mark.parametrize("change", [{"org_id": "org_acme"}, {"limits": {"max_steps": 1}}, {"version": "two"}])
def test_a_record_refuses_unknown_fields_and_bad_values(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentRecord.model_validate({**RECORD, **change})


def test_the_envelopes_carry_one_record_or_a_list() -> None:
    record = AgentRecord.model_validate(RECORD)
    listed = AgentsEnvelope.model_validate(ok([record]).model_dump())
    single = AgentEnvelope.model_validate(ok(record).model_dump())
    assert listed.data is not None
    assert single.data is not None
    assert listed.data[0] == single.data == record


def test_the_name_pattern_matches_registry_names_only() -> None:
    assert re.fullmatch(AGENT_NAME_PATTERN, "provisioning_agent")
    assert not re.fullmatch(AGENT_NAME_PATTERN, "DB-agent")
