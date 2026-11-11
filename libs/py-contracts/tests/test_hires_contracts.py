"""Hire and PII contracts (HIRE-1): shapes and bounds only; the API validates values (PII-3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from onb_contracts import tenant_inputs_in_model
from onb_contracts.hires import (
    MAX_REASON_LENGTH,
    REVEAL_TTL_SECONDS,
    AlternativeIdType,
    HireEnvelope,
    HireIdProof,
    HirePiiInput,
    HireRecord,
    NewHireRequest,
    PiiAccessActor,
    PiiAccessActorType,
    PiiAccessEnvelope,
    PiiAccessKind,
    PiiAccessRecord,
    PiiField,
    PiiUpdateRequest,
    RevealedValue,
    RevealEnvelope,
    RevealRequest,
)
from pydantic import ValidationError

PTK = "ptk_" + "x" * 8  # a token shape, not a secret
BODY: dict[str, Any] = {
    "name": "Neha Verma",
    "role": "engineer",
    "location": "Bangalore",
    "start_date": "2026-12-07",
    "manager_id": "u_acme_mgr_ravi",
    "pii": {"email": "neha.verma.xt@example.com"},
}


def test_a_new_hire_needs_only_an_email_among_the_sensitive_fields() -> None:
    hire = NewHireRequest.model_validate(BODY)
    assert hire.start_date == date(2026, 12, 7)
    assert hire.pii.mobile is None
    assert hire.aadhaar_consent is None
    assert hire.alternative_id is None
    assert hire.notes == ""


@pytest.mark.parametrize("key", ["org_id", "orgId", "tenant", "salary"])
def test_unknown_fields_and_any_org_are_refused(key: str) -> None:
    with pytest.raises(ValidationError):
        NewHireRequest.model_validate({**BODY, key: "org_globex"})
    with pytest.raises(ValidationError):
        HirePiiInput.model_validate({"email": "a@example.com", key: "x"})


@pytest.mark.parametrize(
    "model", [NewHireRequest, HirePiiInput, PiiUpdateRequest, RevealRequest], ids=lambda m: m.__name__
)
def test_no_request_model_declares_a_tenant_input(model: Any) -> None:
    assert tenant_inputs_in_model(model) == []


@pytest.mark.parametrize(
    "change",
    [
        {"pii": {}},
        {"name": "   "},
        {"manager_id": "has space"},
        {"start_date": "next week"},
        {"alternative_id": "library_card"},
        {"aadhaar_consent": {"purpose": " "}},
    ],
    ids=["no-email", "blank-name", "bad-manager-id", "bad-date", "unknown-id-type", "blank-purpose"],
)
def test_malformed_new_hires_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NewHireRequest.model_validate({**BODY, **change})


@pytest.mark.parametrize("reason", ["", "   ", "x" * (MAX_REASON_LENGTH + 1)], ids=["empty", "blank", "long"])
def test_a_reveal_needs_a_real_reason(reason: str) -> None:
    with pytest.raises(ValidationError):
        RevealRequest.model_validate({"reason": reason})
    with pytest.raises(ValidationError):
        RevealRequest.model_validate({})


def test_a_reason_and_an_update_validate() -> None:
    assert RevealRequest(reason="verifying identity").reason == "verifying identity"
    update = PiiUpdateRequest.model_validate({"value": "+91 90000 00042", "aadhaar_consent": {"purpose": "kyc"}})
    assert update.aadhaar_consent is not None
    assert update.aadhaar_consent.purpose == "kyc"


def test_the_sensitive_fields_and_kinds_are_the_prds() -> None:
    assert {f.value for f in PiiField} == {
        "mobile",
        "email",
        "aadhaar",
        "pan",
        "bank_account",
        "ifsc",
        "account_holder_name",
    }
    assert {t.value for t in AlternativeIdType} == {"passport", "voter_id", "driving_licence"}
    assert {k.value for k in PiiAccessKind} == {"reveal", "resolve"}


def test_records_and_envelopes_round_trip() -> None:
    at = datetime(2026, 11, 1, tzinfo=UTC)
    record = HireRecord(
        id="h_1",
        name="Neha Verma",
        role="engineer",
        location="Bangalore",
        start_date=date(2026, 12, 7),
        manager_id="u_acme_mgr_ravi",
        notes="",
        pii={PiiField.EMAIL: "n***@example.com"},
        id_proof=HireIdProof(aadhaar_consent=False, alternative_id=AlternativeIdType.PASSPORT),
        created_by="u_acme_hr1",
        created_at=at,
    )
    assert HireEnvelope(success=True, data=record).model_dump(mode="json")["data"]["pii"] == {
        "email": "n***@example.com"
    }
    revealed = RevealedValue(
        hire_id="h_1", field=PiiField.PAN, value="x", access_id="a" * 32, expires_in_seconds=REVEAL_TTL_SECONDS
    )
    assert RevealEnvelope(success=True, data=revealed).data == revealed
    entry = PiiAccessRecord(
        id="a" * 32,
        kind=PiiAccessKind.REVEAL,
        actor=PiiAccessActor(type=PiiAccessActorType.USER, id="u_acme_hr1"),
        hire_id="h_1",
        field=PiiField.PAN,
        token=PTK,
        masked="BQXXXXX1K",
        reason="payroll",
        sink=None,
        run_id=None,
        task_id=None,
        at=at,
    )
    assert PiiAccessEnvelope(success=True, data=[entry]).data == [entry]
    with pytest.raises(ValidationError):
        HireRecord.model_validate({**record.model_dump(), "org_id": "org_acme"})
