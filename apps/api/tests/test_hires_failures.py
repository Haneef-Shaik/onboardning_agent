"""Partial failures of hire writes (HIRE-1 review): nothing half-done, no value lost, audit fail-closed.

The vault supersedes a field's previous value as soon as a new one is put, so an update must not reach
the vault unless the hire can take the new token: the email is reserved on the hire first, the audit
record is written first, and the hire takes a token only if it is newer than the one it holds.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_api.auth.tokens import Principal
from onb_api.hires.service import HireService
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_contracts.hires import PiiField, PiiUpdateRequest
from onb_crypto import VaultConflictError

HR = ("u_acme_hr1", "org_acme", Role.HR)
FIRST_EMAIL = "first.xt@example.com"
SECOND_EMAIL = "second.xt@example.com"


def _headers() -> dict[str, str]:
    return bearer(token_for(*HR))


def _create(client: TestClient, email: str) -> str:
    body = {
        "name": "Neha Verma",
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-07",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": email, "mobile": "+91 90000 00042"},
    }
    response = client.post("/hires", json=body, headers=_headers())
    assert response.status_code == 201, response.text
    hire_id: str = response.json()["data"]["id"]
    return hire_id


def _reveal(client: TestClient, hire_id: str, field: str) -> Any:
    return client.post(f"/hires/{hire_id}/pii/{field}/reveal", json={"reason": "check"}, headers=_headers())


def _put(client: TestClient, hire_id: str, field: str, value: str) -> Any:
    return client.put(f"/hires/{hire_id}/pii/{field}", json={"value": value}, headers=_headers())


def _records(db: MemoryDatabase, hire_id: str, field: str) -> list[dict[str, Any]]:
    return [r for r in db.raw("pii_vault") if r["subject"]["id"] == hire_id and r["field"] == field]


def test_losing_the_email_race_on_update_keeps_the_old_value_readable(
    client: TestClient, hires: HireService, memory_db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client, FIRST_EMAIL)
    second = _create(client, SECOND_EMAIL)

    async def never_taken(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setattr(hires.store, "email_taken", never_taken)  # both pass the pre-check
    response = _put(client, second, "email", FIRST_EMAIL)
    assert response.status_code == 409, response.text
    assert [r["status"] for r in _records(memory_db, second, "email")] == ["active"], "the vault was not touched"
    assert _reveal(client, second, "email").json()["data"]["value"] == SECOND_EMAIL


def test_an_older_value_never_replaces_a_newer_one(
    client: TestClient, hires: HireService, memory_db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two updates of one field: the vault orders them v2 < v3, but v3 reaches the hire first. The
    hire keeps v3 (the newest, so readable), and the v2 caller is told to retry."""
    hire_id = _create(client, FIRST_EMAIL)
    original_commit = hires.store.commit_pii
    principal = Principal(user_id="u_acme_hr1", org_id="org_acme", role=Role.HR)
    raced: list[bool] = []

    async def commit_after_a_newer_update(*args: Any, **kwargs: Any) -> bool:
        if not raced:
            raced.append(True)
            await hires.update(principal, hire_id, PiiField.MOBILE, PiiUpdateRequest(value="+91 90000 00099"))
        return await original_commit(*args, **kwargs)

    monkeypatch.setattr(hires.store, "commit_pii", commit_after_a_newer_update)
    response = _put(client, hire_id, "mobile", "+91 90000 00077")
    assert response.status_code == 409, response.text
    assert _reveal(client, hire_id, "mobile").json()["data"]["value"] == "+91 90000 00099"


def test_an_update_whose_audit_fails_changes_nothing(
    client: TestClient, hire_audit: InMemoryAuditLog, memory_db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUD-1: a PII change that cannot be audited is not made."""
    hire_id = _create(client, FIRST_EMAIL)
    before = (memory_db.raw("new_hires"), memory_db.raw("pii_vault"))

    async def broken(**_: Any) -> str:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(hire_audit, "audit", broken)
    assert _put(client, hire_id, "email", SECOND_EMAIL).status_code == 500
    assert (memory_db.raw("new_hires"), memory_db.raw("pii_vault")) == before
    monkeypatch.undo()
    assert _reveal(client, hire_id, "email").json()["data"]["value"] == FIRST_EMAIL


def test_a_concurrent_vault_write_is_a_409_to_retry(
    client: TestClient, hires: HireService, monkeypatch: pytest.MonkeyPatch
) -> None:
    hire_id = _create(client, FIRST_EMAIL)

    async def conflict(*_: Any, **__: Any) -> str:
        raise VaultConflictError("written concurrently; retry")

    monkeypatch.setattr(hires._vault, "put", conflict)
    response = _put(client, hire_id, "mobile", "+91 90000 00077")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_a_create_whose_audit_and_undo_both_fail_still_purges_the_vault(
    client: TestClient,
    hires: HireService,
    hire_audit: InMemoryAuditLog,
    memory_db: MemoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(**_: Any) -> str:
        raise RuntimeError("audit store down")

    async def delete_fails(*_: Any) -> None:
        raise RuntimeError("delete refused")

    monkeypatch.setattr(hire_audit, "audit", broken)
    monkeypatch.setattr(hires.store, "delete", delete_fails)
    body = {
        "name": "Neha Verma",
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-07",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": FIRST_EMAIL},
    }
    assert client.post("/hires", json=body, headers=_headers()).status_code == 500
    assert {r["status"] for r in memory_db.raw("pii_vault")} == {"purged"}
