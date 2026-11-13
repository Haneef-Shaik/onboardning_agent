"""A reveal racing an update of the same field (FIX-1; PRD FR-P2, FR-P4).

PERF-1 found `POST /hires/{id}/pii/{field}/reveal` answering 500 while another request updated that field
(`docs/evidence/PERF-1/concurrent-reveal-500.txt`): the reveal read the hire's token, the update superseded
it in the vault, and the vault refused the old token (`SupersededTokenError`). A superseded token is not a
broken vault: the reveal answers 200 with the current value, or 409 when the field keeps changing under
it, and both are audited. It is never a 500.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from onb_api.auth.tokens import Principal
from onb_api.hires.service import HireService
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_contracts.hires import PiiField, PiiUpdateRequest
from onb_data import AuditOutcome, AuditQuery, tenant_scope

HR: Final = ("u_acme_hr1", "org_acme", Role.HR)
PRINCIPAL: Final = Principal(user_id="u_acme_hr1", org_id="org_acme", role=Role.HR)
FIRST: Final = "+91 90000 00042"
RACES: Final = 20


def _headers() -> dict[str, str]:
    return bearer(token_for(*HR))


def _create(client: TestClient) -> str:
    body = {
        "name": "Neha Verma",
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-07",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": "neha.race@example.com", "mobile": FIRST},
    }
    response = client.post("/hires", json=body, headers=_headers())
    assert response.status_code == 201, response.text
    hire_id: str = response.json()["data"]["id"]
    return hire_id


def _reveal(client: TestClient, hire_id: str) -> Any:
    return client.post(f"/hires/{hire_id}/pii/mobile/reveal", json={"reason": "race check"}, headers=_headers())


def _mobile(n: int) -> str:
    return f"+91 90000 {n:05d}"


def _reveal_audits(audit: InMemoryAuditLog, hire_id: str) -> list[tuple[str, str | None]]:
    with tenant_scope("org_acme"):
        entries = asyncio.run(audit.list(AuditQuery(action="pii.reveal"), page=1, limit=100)).entries
    return [(e.outcome.value, e.reason) for e in entries if e.target == f"hire:{hire_id}/mobile"]


def _reveal_log(db: MemoryDatabase, hire_id: str) -> list[dict[str, Any]]:
    return [r for r in db.raw("pii_access_log") if r.get("kind") == "reveal" and r["subject"]["id"] == hire_id]


def _race_resolve(
    hires: HireService, monkeypatch: pytest.MonkeyPatch, update: Any, *, times: int, start: int = 100
) -> list[str]:
    """Before each of the first `times` vault reads, `update` changes the field: the token the reveal holds is
    superseded by the time the vault reads it. Returns the values the updates wrote."""
    vault = hires._vault  # noqa: SLF001 - the race is between the route's read of the hire and the vault's read
    original = vault.resolve
    written: list[str] = []

    async def resolve(token: str, **kwargs: Any) -> str:
        if len(written) < times:
            value = _mobile(start + len(written))
            written.append(value)
            await update(value)
        return await original(token, **kwargs)

    monkeypatch.setattr(vault, "resolve", resolve)
    return written


def test_a_reveal_racing_a_finished_update_returns_the_current_value_and_is_audited(
    client: TestClient,
    hires: HireService,
    hire_audit: InMemoryAuditLog,
    memory_db: MemoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hire_id = _create(client)

    async def update(value: str) -> None:
        await hires.update(PRINCIPAL, hire_id, PiiField.MOBILE, PiiUpdateRequest(value=value))

    written = _race_resolve(hires, monkeypatch, update, times=1)
    response = _reveal(client, hire_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["value"] == written[-1], "the current value, never the superseded one"
    assert _reveal_audits(hire_audit, hire_id) == [("success", "race check")]
    [entry] = _reveal_log(memory_db, hire_id)  # the value left with its access-log entry
    [hire] = [h for h in memory_db.raw("new_hires") if h["_id"] == hire_id]
    assert entry["token"] == hire["pii"]["mobile"], "the entry names the token that was read: the current one"


def test_a_reveal_racing_an_update_still_in_flight_is_a_409_and_is_audited(
    client: TestClient,
    hires: HireService,
    hire_audit: InMemoryAuditLog,
    memory_db: MemoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The update has put the new value in the vault but the hire does not hold its token yet: nothing current
    can be read, so the caller is told to retry."""
    hire_id = _create(client)

    async def put_only(value: str) -> None:
        await hires._vault.put(  # noqa: SLF001 - the first half of an update: vault written, hire not yet
            "org_acme", subject_id=hire_id, field=_vault_mobile(), value=value, created_by="u_other", consent=None
        )

    _race_resolve(hires, monkeypatch, put_only, times=1)
    response = _reveal(client, hire_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"
    assert _reveal_audits(hire_audit, hire_id) == [("failed", "the value changed meanwhile")]
    assert _reveal_log(memory_db, hire_id) == [], "no value left, so no access-log entry"


def test_reveals_racing_updates_of_the_same_field_never_answer_500(
    client: TestClient, hires: HireService, hire_audit: InMemoryAuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many reveals, each raced by 0 to 4 finished updates: every answer is 200 with the current value or a
    409, never a 500, and every one is audited."""
    hire_id = _create(client)
    answers: list[int] = []
    current = FIRST

    async def update(value: str) -> None:
        await hires.update(PRINCIPAL, hire_id, PiiField.MOBILE, PiiUpdateRequest(value=value))

    for race in range(RACES):
        with monkeypatch.context() as patch:
            written = _race_resolve(hires, patch, update, times=race % 5, start=race * 10)
            response = _reveal(client, hire_id)
        current = written[-1] if written else current
        assert response.status_code in (200, 409), f"race {race}: {response.status_code} {response.text}"
        if response.status_code == 200:
            assert response.json()["data"]["value"] == current, f"race {race}: not the current value"
        answers.append(response.status_code)
    audits = _reveal_audits(hire_audit, hire_id)
    assert len(audits) == RACES, "every reveal, answered 200 or 409, is audited"
    assert [o for o, _ in audits].count("success") == answers.count(200)
    assert [o for o, _ in audits].count("failed") == answers.count(409)


def _vault_mobile() -> Any:
    from onb_api.hires import fields

    return fields.vault_field(PiiField.MOBILE)
