"""Hires with PII held as tokens, and reveal with a reason (HIRE-1; PRD FR-R1, FR-P1/P2/P4/P5, §7.10).

Every value here is synthetic (PII-3's `9999…` Aadhaar range, the `90000` mobile range, example.com).
The app runs over the in-memory database (`onb_api.testing_mongo`), so the vault, the keyring, tenant
scoping and the hire store all run their production code.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_api.hires.service import HireService
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_crypto import MaskedField, is_token, mask
from onb_data import AuditOutcome, AuditQuery, tenant_scope

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
GLOBEX_ADMIN = ("u_globex_admin", "org_globex", Role.HR_ADMIN)

# Synthetic values in the shapes HR types them.
RAW = {
    "email": "Neha.Verma.XT@example.com",
    "mobile": "+91 90000 00042",
    "aadhaar": "9999 0000 4202",
    "pan": "BQVPS4821K",
    "bank_account": "000123456789012",
    "ifsc": "HDFC0001234",
    "account_holder_name": "Lotus Grove Holdings",
}
# The canonical forms the vault and indexes see: none of these may appear outside the vault either.
CANONICAL = {"neha.verma.xt@example.com", "9000000042", "999900004202"}
VAULT_PARTITIONS = {"pii_vault", "pii_vault_aadhaar", "org_keys"}


def _headers(actor: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*actor))


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Neha Verma",
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-07",
        "manager_id": "u_acme_mgr_ravi",
        "notes": "Joins the platform team.",
        "pii": dict(RAW),
        "aadhaar_consent": {"purpose": "statutory onboarding records"},
    }
    return {**body, **overrides}


def _create(client: TestClient, actor: tuple[str, str, Role] = HR, **overrides: Any) -> Any:
    return client.post("/hires", json=_body(**overrides), headers=_headers(actor))


def _created_id(client: TestClient, actor: tuple[str, str, Role] = HR, **overrides: Any) -> str:
    response = _create(client, actor, **overrides)
    assert response.status_code == 201, response.text
    hire_id: str = response.json()["data"]["id"]
    return hire_id


def _reveal(
    client: TestClient, hire_id: str, field: str, actor: tuple[str, str, Role], reason: Any = "checking"
) -> Any:
    body = {} if reason is None else {"reason": reason}
    return client.post(f"/hires/{hire_id}/pii/{field}/reveal", json=body, headers=_headers(actor))


def _raw_values() -> set[str]:
    return {*RAW.values(), *CANONICAL}


def _leaks(text: str) -> list[str]:
    return sorted(v for v in _raw_values() if v in text)


# --- POST /hires: values to the vault, tokens and blind indexes on the hire (box 1) -------------------


def test_hr_creates_a_hire_and_sees_every_field_masked(client: TestClient) -> None:
    response = _create(client)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["id"].startswith("h_")
    assert data["pii"] == {
        "email": mask(MaskedField.EMAIL, RAW["email"]),
        "mobile": mask(MaskedField.MOBILE, RAW["mobile"]),
        "aadhaar": mask(MaskedField.AADHAAR, RAW["aadhaar"]),
        "pan": mask(MaskedField.PAN, RAW["pan"]),
        "bank_account": mask(MaskedField.BANK_ACCOUNT, RAW["bank_account"]),
        "ifsc": "[masked]",
        "account_holder_name": "[masked]",
    }
    assert data["id_proof"] == {"aadhaar_consent": True, "alternative_id": None}
    assert data["created_by"] == "u_acme_hr1"
    assert _leaks(response.text) == []


def test_after_post_hires_no_raw_value_is_in_new_hires_or_the_logs(
    client: TestClient, memory_db: MemoryDatabase, hire_audit: InMemoryAuditLog, caplog: pytest.LogCaptureFixture
) -> None:
    """Box 1, the scan test: outside the vault partitions, only tokens, masks and blind indexes."""
    caplog.set_level(logging.DEBUG)
    hire_id = _created_id(client)
    stored = next(d for d in memory_db.raw("new_hires") if d["_id"] == hire_id)
    assert set(stored["pii"]) == set(RAW)
    assert all(is_token(token) for token in stored["pii"].values())
    for index in ("email_hash", "mobile_hash", "pan_hash", "aadhaar_hash"):
        assert re.fullmatch(r"[0-9a-f]{64}", stored[index]), index
    outside_vault = [doc for name, doc in memory_db.all_documents() if name not in VAULT_PARTITIONS]
    assert _leaks(repr(outside_vault)) == []
    with tenant_scope("org_acme"):
        audit = asyncio.run(hire_audit.list(AuditQuery(), page=1, limit=50))
    assert _leaks(repr(audit.entries)) == []
    assert _leaks(caplog.text) == []
    assert caplog.records, "the scan needs the request's log lines to look at"


def test_the_vault_holds_each_value_bound_to_the_hire(client: TestClient, memory_db: MemoryDatabase) -> None:
    hire_id = _created_id(client)
    records = memory_db.raw("pii_vault") + memory_db.raw("pii_vault_aadhaar")
    assert {(r["subject"]["id"], r["field"]) for r in records} == {(hire_id, f) for f in RAW}
    assert {r["org_id"] for r in records} == {"org_acme"}
    assert [r["field"] for r in memory_db.raw("pii_vault_aadhaar")] == ["aadhaar"]
    assert len(memory_db.raw("pii_consent")) == 1


def test_free_text_notes_are_stored_redacted(client: TestClient, memory_db: MemoryDatabase) -> None:
    hire_id = _created_id(client, notes="Call her on 9000000042 before day one.")
    stored = next(d for d in memory_db.raw("new_hires") if d["_id"] == hire_id)
    assert "9000000042" not in stored["notes"]
    assert "[MOBILE]" in stored["notes"]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("aadhaar", "9999 0000 4203"),  # Verhoeff fails
        ("aadhaar", "1999 0000 4202"),  # starts with 1
        ("pan", "BQVPS48210"),
        ("mobile", "+91 50000 00042"),
        ("email", "neha.verma@"),
        ("bank_account", "12345"),
        ("ifsc", "HDFC1001234"),
    ],
)
def test_an_invalid_value_is_refused_before_anything_is_stored(
    client: TestClient, memory_db: MemoryDatabase, field: str, bad: str
) -> None:
    """PII-3: validate first; a refused value is never masked, indexed, stored or echoed."""
    before = memory_db.raw("new_hires")
    response = _create(client, pii={**RAW, field: bad})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [{"field": f"body.pii.{field}", "issue": f"Not a valid {field}"}]
    assert bad not in response.text
    assert memory_db.raw("new_hires") == before
    assert memory_db.raw("pii_vault") == []
    assert memory_db.raw("pii_vault_aadhaar") == []


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"aadhaar_consent": None}, "body.aadhaar_consent"),
        ({"pii": {k: v for k, v in RAW.items() if k != "aadhaar"}}, "body.aadhaar_consent"),
        ({"alternative_id": "passport"}, "body.alternative_id"),
    ],
    ids=["aadhaar-without-consent", "consent-without-aadhaar", "alternative-id-beside-aadhaar"],
)
def test_aadhaar_needs_consent_and_excludes_an_alternative_id(
    client: TestClient, memory_db: MemoryDatabase, overrides: dict[str, Any], field: str
) -> None:
    response = _create(client, **overrides)
    assert response.status_code == 422, response.text
    assert [d["field"] for d in response.json()["error"]["details"]] == [field]
    assert memory_db.raw("pii_vault_aadhaar") == []


def test_a_hire_who_declines_aadhaar_gives_an_alternative_id(client: TestClient, memory_db: MemoryDatabase) -> None:
    pii = {k: v for k, v in RAW.items() if k != "aadhaar"}
    response = _create(client, pii=pii, aadhaar_consent=None, alternative_id="passport")
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["id_proof"] == {"aadhaar_consent": False, "alternative_id": "passport"}
    assert "aadhaar" not in data["pii"]
    assert memory_db.raw("pii_vault_aadhaar") == []


@pytest.mark.parametrize(
    "manager_id", ["u_acme_hr1", "u_globex_mgr1", "u_nobody"], ids=["not-a-manager", "org-b", "unknown"]
)
def test_the_manager_must_be_a_manager_of_the_callers_org(client: TestClient, manager_id: str) -> None:
    response = _create(client, manager_id=manager_id)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [{"field": "body.manager_id", "issue": "Not a manager in this org"}]


def test_a_manager_cannot_create_a_hire(client: TestClient, memory_db: MemoryDatabase) -> None:
    before = memory_db.raw("new_hires")
    assert _create(client, MANAGER).status_code == 403
    assert memory_db.raw("new_hires") == before


def test_org_keys_are_made_on_the_first_sensitive_write(client: TestClient, memory_db: MemoryDatabase) -> None:
    assert memory_db.raw("org_keys") == []
    _created_id(client)
    _created_id(client, pii={**RAW, "email": "second.xt@example.com"})
    assert [(k["org_id"], k["key_version"]) for k in memory_db.raw("org_keys")] == [("org_acme", 1)]


# --- dedupe by blind index (box 4) -----------------------------------------------------------------


def test_a_duplicate_email_in_the_same_org_is_refused(client: TestClient, memory_db: MemoryDatabase) -> None:
    _created_id(client)
    hires_before, vault_before = memory_db.raw("new_hires"), memory_db.raw("pii_vault")
    # The same mailbox typed differently is the same email.
    duplicate = _create(client, ADMIN, pii={**RAW, "email": "  neha.verma.xt@EXAMPLE.com "})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "CONFLICT"
    assert memory_db.raw("new_hires") == hires_before
    assert memory_db.raw("pii_vault") == vault_before


def test_the_same_email_in_another_org_is_accepted(client: TestClient, memory_db: MemoryDatabase) -> None:
    _created_id(client)
    other = _create(client, GLOBEX_HR, manager_id="u_globex_mgr1")
    assert other.status_code == 201, other.text
    hashes = {(d["org_id"], d["email_hash"]) for d in memory_db.raw("new_hires") if "email_hash" in d}
    assert len(hashes) == 2
    assert len({h for _, h in hashes}) == 2, "per-org index keys: one email indexes differently per org"


def test_the_unique_index_refuses_a_duplicate_that_slipped_past_the_check(
    client: TestClient, hires: HireService, memory_db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent creates both pass the pre-check; the `{org_id, email_hash}` index refuses the
    second, and its vault values are purged."""
    _created_id(client)

    async def never_taken(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setattr(hires.store, "email_taken", never_taken)
    response = _create(client)
    assert response.status_code == 409, response.text
    assert len([d for d in memory_db.raw("new_hires") if "email_hash" in d]) == 1
    statuses = [r["status"] for r in memory_db.raw("pii_vault") if r["field"] == "email"]
    assert sorted(statuses) == ["active", "purged"]


# --- GET /hires, GET /hires/{id}: masked, managers see only their reports ------------------------------


def test_hr_lists_the_orgs_hires_masked(client: TestClient) -> None:
    hire_id = _created_id(client)
    response = client.get("/hires", headers=_headers(HR))
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {h["id"] for h in body["data"]}
    assert ids == {"h_acme_001", "h_acme_002", "h_acme_003", "h_acme_004", hire_id}
    assert body["meta"]["page"] == {"total": 5, "page": 1, "limit": 50}
    assert _leaks(response.text) == []
    seeded = next(h for h in body["data"] if h["id"] == "h_acme_001")
    assert set(seeded["pii"].values()) == {"[masked]"}, "a seeded token shows as masked, never as itself"


def test_a_manager_lists_only_their_reports(client: TestClient) -> None:
    hire_id = _created_id(client)
    _created_id(client, pii={**RAW, "email": "other.xt@example.com"}, manager_id="u_acme_mgr_ravi")
    response = client.get("/hires", headers=_headers(MANAGER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert {h["manager_id"] for h in data} == {"u_acme_mgr_ravi"}
    assert hire_id in {h["id"] for h in data}
    assert "h_acme_002" not in {h["id"] for h in data}  # Anita's report


def test_list_pages(client: TestClient) -> None:
    response = client.get("/hires", params={"page": 2, "limit": 2}, headers=_headers(ADMIN))
    assert response.json()["meta"]["page"] == {"total": 4, "page": 2, "limit": 2}
    assert len(response.json()["data"]) == 2


def test_get_one_hire_masked(client: TestClient) -> None:
    hire_id = _created_id(client)
    response = client.get(f"/hires/{hire_id}", headers=_headers(MANAGER))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["pii"]["mobile"] == mask(MaskedField.MOBILE, RAW["mobile"])
    assert _leaks(response.text) == []


def test_another_managers_report_or_another_orgs_hire_is_not_found(client: TestClient) -> None:
    missing = client.get("/hires/h_nobody", headers=_headers(MANAGER))
    others_report = client.get("/hires/h_acme_002", headers=_headers(MANAGER))
    foreign = client.get("/hires/h_globex_001", headers=_headers(HR))
    for response in (others_report, foreign):
        assert response.status_code == 404
        assert response.json()["error"] == missing.json()["error"]


# --- reveal: roles, reason, Aadhaar for HR_ADMIN only (box 2) ------------------------------------------


def test_a_manager_reveal_is_403(client: TestClient) -> None:
    hire_id = _created_id(client)
    response = _reveal(client, hire_id, "mobile", MANAGER)
    assert response.status_code == 403
    assert _leaks(response.text) == []


def test_an_hr_reveal_of_aadhaar_is_403_and_audited_as_denied(client: TestClient, hire_audit: InMemoryAuditLog) -> None:
    hire_id = _created_id(client)
    response = _reveal(client, hire_id, "aadhaar", HR, reason="verifying identity")
    assert response.status_code == 403
    assert _leaks(response.text) == []
    with tenant_scope("org_acme"):
        denied = asyncio.run(hire_audit.list(AuditQuery(outcome=AuditOutcome.DENIED), page=1, limit=10)).entries
    assert [(e.action, e.target, e.actor.id) for e in denied] == [
        ("pii.reveal", f"hire:{hire_id}/aadhaar", "u_acme_hr1")
    ]


def test_hr_admin_reveals_aadhaar_with_a_reason(client: TestClient) -> None:
    hire_id = _created_id(client)
    response = _reveal(client, hire_id, "aadhaar", ADMIN, reason="verifying identity")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["hire_id"], data["field"], data["value"]) == (hire_id, "aadhaar", "999900004202")
    assert data["expires_in_seconds"] == 30
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("field", ["email", "mobile", "pan", "bank_account", "ifsc", "account_holder_name"])
def test_hr_reveals_one_other_field_with_a_reason(client: TestClient, field: str) -> None:
    hire_id = _created_id(client)
    response = _reveal(client, hire_id, field, HR)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["field"] == field
    assert response.json()["data"]["value"] in {RAW[field], RAW[field].strip()}


@pytest.mark.parametrize("reason", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_a_reveal_without_a_reason_is_422(client: TestClient, reason: Any) -> None:
    hire_id = _created_id(client)
    response = _reveal(client, hire_id, "aadhaar", ADMIN, reason=reason)
    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.reason"]
    assert _leaks(response.text) == []


def test_revealing_a_field_the_hire_lacks_or_an_unknown_hire_is_404(client: TestClient) -> None:
    hire_id = _created_id(client, pii={k: v for k, v in RAW.items() if k != "aadhaar"}, aadhaar_consent=None)
    assert _reveal(client, hire_id, "aadhaar", ADMIN).status_code == 404
    assert _reveal(client, "h_nobody", "email", ADMIN).status_code == 404
    assert _reveal(client, "h_globex_001", "email", ADMIN).status_code == 404


def test_a_seeded_stand_in_token_does_not_reveal(client: TestClient) -> None:
    response = _reveal(client, "h_acme_001", "email", ADMIN)
    assert response.status_code == 404
    assert "ptk_seed" not in response.text


def test_an_unknown_field_is_422(client: TestClient) -> None:
    hire_id = _created_id(client)
    assert _reveal(client, hire_id, "salary", ADMIN).status_code == 422


# --- every reveal is logged, readable through GET /pii-access-log (box 3) ------------------------------


def _access_log(client: TestClient, actor: tuple[str, str, Role] = ADMIN, **params: Any) -> Any:
    return client.get("/pii-access-log", params=params, headers=_headers(actor))


def test_every_reveal_writes_pii_access_log_readable_by_hr_admin(client: TestClient) -> None:
    hire_id = _created_id(client)
    reveals = [("email", HR, "sending the offer"), ("pan", HR, "payroll setup"), ("aadhaar", ADMIN, "kyc")]
    access_ids = []
    for field, actor, reason in reveals:
        response = _reveal(client, hire_id, field, actor, reason=reason)
        assert response.status_code == 200, response.text
        access_ids.append(response.json()["data"]["access_id"])
    response = _access_log(client)
    assert response.status_code == 200, response.text
    entries = response.json()["data"]
    assert [e["id"] for e in entries] == access_ids[::-1], "newest first, one entry per reveal"
    assert [(e["kind"], e["hire_id"], e["field"], e["actor"]["id"], e["reason"]) for e in entries] == [
        ("reveal", hire_id, field, actor[0], reason) for field, actor, reason in reveals[::-1]
    ]
    assert entries[0]["masked"] == mask(MaskedField.AADHAAR, RAW["aadhaar"])
    assert all(is_token(e["token"]) for e in entries)
    assert response.json()["meta"]["page"]["total"] == 3
    assert _leaks(response.text) == []


def test_refused_reveals_write_no_access_entry(client: TestClient) -> None:
    hire_id = _created_id(client)
    _reveal(client, hire_id, "aadhaar", HR)
    _reveal(client, hire_id, "mobile", MANAGER)
    _reveal(client, hire_id, "mobile", ADMIN, reason=None)
    assert _access_log(client).json()["data"] == []


@pytest.mark.parametrize("actor", [HR, MANAGER], ids=["HR", "MANAGER"])
def test_only_hr_admin_reads_the_access_log(client: TestClient, actor: tuple[str, str, Role]) -> None:
    assert _access_log(client, actor).status_code == 403


def test_the_access_log_filters_by_hire_and_field_and_stays_in_the_org(client: TestClient) -> None:
    first = _created_id(client)
    second = _created_id(client, pii={**RAW, "email": "other.xt@example.com"})
    _reveal(client, first, "email", HR)
    _reveal(client, second, "mobile", HR)
    globex = _created_id(client, GLOBEX_HR, manager_id="u_globex_mgr1")
    _reveal(client, globex, "email", GLOBEX_HR)
    assert [e["hire_id"] for e in _access_log(client, hire_id=first).json()["data"]] == [first]
    assert [e["field"] for e in _access_log(client, field="mobile").json()["data"]] == ["mobile"]
    assert {e["hire_id"] for e in _access_log(client).json()["data"]} == {first, second}
    assert [e["hire_id"] for e in _access_log(client, GLOBEX_ADMIN).json()["data"]] == [globex]


def test_a_reason_carrying_pii_is_redacted_in_the_logs(client: TestClient, hire_audit: InMemoryAuditLog) -> None:
    hire_id = _created_id(client)
    _reveal(client, hire_id, "mobile", HR, reason="she asked from 9000000042")
    entry = _access_log(client).json()["data"][0]
    assert entry["reason"] == "she asked from [MOBILE]"
    with tenant_scope("org_acme"):
        reveal = asyncio.run(hire_audit.list(AuditQuery(action="pii.reveal"), page=1, limit=10)).entries
    assert [e.reason for e in reveal] == ["she asked from [MOBILE]"]


def test_the_access_log_lists_sink_resolutions_too(client: TestClient, memory_db: MemoryDatabase) -> None:
    """PII-4's sink writes `kind: resolve` entries; HR_ADMIN sees them beside reveals (FR-P5)."""
    hire_id = _created_id(client)
    memory_db["pii_access_log"].documents.append(
        {
            "_id": "sink-entry-1",
            "org_id": "org_acme",
            "kind": "resolve",
            "actor": {"type": "service", "id": "notify.telegram"},
            "sink": "notify.telegram",
            "run_id": "run_x",
            "task_id": "t_1",
            "jti": "jti_1",
            "subject": {"type": "new_hire", "id": hire_id},
            "field": "mobile",
            "token": "ptk_01JD0000000000000000000000AAAAAAAAAAAAAAAAAAAAAAAAAA",
            "masked": "90XXXXXX42",
            "at": datetime(2026, 11, 1, tzinfo=UTC),
        }
    )
    entries = _access_log(client).json()["data"]
    assert [(e["kind"], e["sink"], e["actor"]["type"], e["reason"]) for e in entries] == [
        ("resolve", "notify.telegram", "service", None)
    ]


def test_no_value_is_released_when_the_access_log_write_fails(client: TestClient, memory_db: MemoryDatabase) -> None:
    hire_id = _created_id(client)
    memory_db["pii_access_log"].fail_next_insert = RuntimeError("the database refused the write")
    response = _reveal(client, hire_id, "email", HR)
    assert response.status_code == 500
    assert _leaks(response.text) == []


def test_no_value_is_released_when_the_audit_write_fails(
    client: TestClient, hire_audit: InMemoryAuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    hire_id = _created_id(client)

    async def broken(**_: Any) -> str:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(hire_audit, "audit", broken)
    response = _reveal(client, hire_id, "email", HR)
    assert response.status_code == 500
    assert _leaks(response.text) == []


# --- PUT /hires/{id}/pii/{field}: HR only, a new version and token (FR-P4) -----------------------------


def _put(client: TestClient, hire_id: str, field: str, body: dict[str, Any], actor: tuple[str, str, Role] = HR) -> Any:
    return client.put(f"/hires/{hire_id}/pii/{field}", json=body, headers=_headers(actor))


def test_hr_updates_a_value_as_a_new_version_and_token(client: TestClient, memory_db: MemoryDatabase) -> None:
    hire_id = _created_id(client)
    old_token = next(d for d in memory_db.raw("new_hires") if d["_id"] == hire_id)["pii"]["mobile"]
    response = _put(client, hire_id, "mobile", {"value": "+91 90000 00077"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["pii"]["mobile"] == mask(MaskedField.MOBILE, "+91 90000 00077")
    stored = next(d for d in memory_db.raw("new_hires") if d["_id"] == hire_id)
    assert stored["pii"]["mobile"] != old_token
    assert is_token(stored["pii"]["mobile"])
    versions = {r["token"]: r["status"] for r in memory_db.raw("pii_vault") if r["field"] == "mobile"}
    assert versions == {old_token: "superseded", stored["pii"]["mobile"]: "active"}
    assert _reveal(client, hire_id, "mobile", HR).json()["data"]["value"] == "+91 90000 00077"
    assert "9000000077" not in repr(memory_db.raw("new_hires"))


def test_a_manager_cannot_update_a_value(client: TestClient) -> None:
    hire_id = _created_id(client)
    assert _put(client, hire_id, "mobile", {"value": "+91 90000 00077"}, MANAGER).status_code == 403


def test_an_invalid_update_is_refused_before_the_vault(client: TestClient, memory_db: MemoryDatabase) -> None:
    hire_id = _created_id(client)
    before = memory_db.raw("pii_vault_aadhaar")
    response = _put(client, hire_id, "aadhaar", {"value": "9999 0000 4203", "aadhaar_consent": {"purpose": "kyc"}})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "body.value", "issue": "Not a valid aadhaar"}]
    assert memory_db.raw("pii_vault_aadhaar") == before


def test_an_aadhaar_update_needs_consent(client: TestClient) -> None:
    hire_id = _created_id(client)
    response = _put(client, hire_id, "aadhaar", {"value": "9999 0000 7779"})
    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.aadhaar_consent"]


def test_updating_to_another_hires_email_is_409(client: TestClient) -> None:
    _created_id(client)
    second = _created_id(client, pii={**RAW, "email": "other.xt@example.com"})
    response = _put(client, second, "email", {"value": RAW["email"]})
    assert response.status_code == 409


def test_updating_an_unknown_or_foreign_hire_is_404(client: TestClient, memory_db: MemoryDatabase) -> None:
    for hire_id in ("h_nobody", "h_globex_001"):
        assert _put(client, hire_id, "mobile", {"value": "+91 90000 00077"}).status_code == 404
    assert memory_db.raw("pii_vault") == []


def test_an_update_is_audited_without_the_value(client: TestClient, hire_audit: InMemoryAuditLog) -> None:
    hire_id = _created_id(client)
    _put(client, hire_id, "mobile", {"value": "+91 90000 00077"})
    with tenant_scope("org_acme"):
        updates = asyncio.run(hire_audit.list(AuditQuery(action="pii.update"), page=1, limit=10)).entries
    assert [(e.target, e.outcome) for e in updates] == [(f"hire:{hire_id}/mobile", AuditOutcome.SUCCESS)]
    assert "9000000077" not in repr(updates)


def test_without_a_hire_service_the_routes_answer_503(users: Any, registry: Any) -> None:
    from onb_api import create_app
    from onb_api.testing import TEST_SETTINGS

    with TestClient(create_app(auth=TEST_SETTINGS, users=users, agents=registry)) as bare:
        assert bare.get("/hires", headers=_headers(HR)).status_code == 503
        assert bare.get("/pii-access-log", headers=_headers(ADMIN)).status_code == 503
