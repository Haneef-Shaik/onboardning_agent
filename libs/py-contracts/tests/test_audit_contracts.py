"""Audit record contracts (AUD-1; PRD §10 `GET /audit`)."""

from datetime import UTC, datetime

import pytest
from onb_contracts import ok
from onb_contracts.audit import AuditActor, AuditActorType, AuditOutcome, AuditRecord, AuditRecordsEnvelope
from pydantic import ValidationError

RECORD = {
    "id": "a" * 32,
    "org_id": "org_acme",
    "actor": {"type": "agent", "id": "db_agent"},
    "action": "mcp.tool_call",
    "target": "find:pii_vault",
    "outcome": "denied",
    "reason": "vault collection",
    "trace_id": "b" * 32,
    "at": "2026-11-10T09:30:00Z",
}


def test_a_record_round_trips_with_utc_time() -> None:
    record = AuditRecord.model_validate(RECORD)
    assert record.actor == AuditActor(type=AuditActorType.AGENT, id="db_agent")
    assert record.outcome is AuditOutcome.DENIED
    assert record.at == datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
    assert record.model_dump(mode="json") == RECORD


@pytest.mark.parametrize("change", [{"extra": 1}, {"outcome": "maybe"}, {"actor": {"type": "robot", "id": "x"}}])
def test_a_record_refuses_unknown_fields_and_values(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**RECORD, **change})


def test_the_page_envelope_carries_records() -> None:
    envelope = AuditRecordsEnvelope.model_validate(ok([AuditRecord.model_validate(RECORD)]).model_dump())
    assert envelope.data is not None
    assert envelope.data[0].id == RECORD["id"]
