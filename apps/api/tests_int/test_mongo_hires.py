"""Hires and PII against a real MongoDB (HIRE-1 boxes 1, 3, 4; PRD §7.10, FR-P5).

Each test uses a scratch database on the compose Mongo, dropped afterwards. seed_v1's hires are
stored first (they have no `email_hash`), so the partial unique index is built over real seeded data.
Read-backs use the root client inside `unscoped(...)`, so nothing trusts the API to report on itself.
All values are synthetic (PII-3's `9999…` Aadhaar range, the `90000` mobile range, example.com).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.hires.service import HireService
from onb_api.testing import TEST_SETTINGS, bearer, seeded_store, token_for
from onb_contracts.auth import Role
from onb_crypto import ORG_KEYS_COLLECTION, Keyring, MasterKey, RepositoryOrgKeyStore, is_token
from onb_data import AuditLog, connect
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]
SEED = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "seed_v1.json"

HR = ("u_acme_hr1", "org_acme", Role.HR)
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
RAW = {
    "email": "Neha.Verma.XT@example.com",
    "mobile": "+91 90000 00042",
    "aadhaar": "9999 0000 4202",
    "pan": "BQVPS4821K",
    "bank_account": "000123456789012",
    "ifsc": "HDFC0001234",
    "account_holder_name": "Lotus Grove Holdings",
}
# Every layout a value could be stored in: as typed, canonical, and per digit group.
NEEDLES = {*RAW.values(), "neha.verma.xt@example.com", "9000000042", "999900004202"}


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client, unscoped: Unscoped) -> Iterator[tuple[str, str]]:
    name = f"it_hire1_{uuid.uuid4().hex[:12]}"
    seeded = json.loads(SEED.read_text())["new_hires"]
    with unscoped("seeding seed_v1's hires of both orgs as stored documents, before the API starts"):
        root_client[name]["new_hires"].insert_many([dict(doc) for doc in seeded])
    yield _uri(root_client), name
    root_client.drop_database(name)


@pytest.fixture
def api(scratch: tuple[str, str]) -> Iterator[TestClient]:
    store = connect(*scratch)
    master = MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    keyring = Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    users = asyncio.run(seeded_store())
    audit = AuditLog(store)
    hires = HireService.build(store, keyring, auditor=audit, users=users)
    with TestClient(create_app(auth=TEST_SETTINGS, users=users, audit=audit, hires=hires)) as client:
        yield client


def _create(api: TestClient, actor: tuple[str, str, Role] = HR, **overrides: Any) -> Any:
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
    return api.post("/hires", json={**body, **overrides}, headers=bearer(token_for(*actor)))


def _everything(root_client: Client, database: str) -> dict[str, list[dict[str, Any]]]:
    db = root_client[database]
    return {name: list(db[name].find({})) for name in sorted(db.list_collection_names())}


def _leaks(text: str) -> list[str]:
    return sorted(needle for needle in NEEDLES if needle in text)


def test_after_post_hires_no_raw_value_is_stored_outside_the_vault_ciphertext_or_logged(
    api: TestClient,
    scratch: tuple[str, str],
    root_client: Client,
    unscoped: Unscoped,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Box 1, the scan test, on Mongo: every collection of the database, the vault included, holds
    no raw value in any layout (the vault holds ciphertext), and neither do the captured logs (DEBUG,
    every logger, PyMongo's command log included)."""
    caplog.set_level(logging.DEBUG)
    response = _create(api)
    assert response.status_code == 201, response.text
    hire_id = response.json()["data"]["id"]
    with unscoped("root scan of every collection for raw values after POST /hires"):
        everything = _everything(root_client, scratch[1])
    assert _leaks(repr(everything)) == []
    stored = next(d for d in everything["new_hires"] if d["_id"] == hire_id)
    assert stored["org_id"] == "org_acme"
    assert set(stored["pii"]) == set(RAW)
    assert all(is_token(token) for token in stored["pii"].values())
    for index in ("email_hash", "mobile_hash", "pan_hash", "aadhaar_hash"):
        assert re.fullmatch(r"[0-9a-f]{64}", stored[index]), index
    vault = everything["pii_vault"] + everything["pii_vault_aadhaar"]
    assert {(v["subject"]["id"], v["field"]) for v in vault} == {(hire_id, f) for f in RAW}
    assert _leaks(caplog.text) == []
    assert any(r.name.startswith("onb_api") for r in caplog.records), "the scan needs the app's own log lines"


def test_the_email_index_is_unique_per_org_and_partial(
    api: TestClient, scratch: tuple[str, str], root_client: Client, unscoped: Unscoped
) -> None:
    """Box 4 on the real index: a duplicate email in one org is refused, the same email in another
    org is accepted, and seeded hires without an `email_hash` do not collide with each other."""
    assert _create(api).status_code == 201
    duplicate = _create(api, ADMIN, pii={**RAW, "email": " neha.verma.xt@EXAMPLE.com"})
    assert duplicate.status_code == 409, duplicate.text
    other_org = _create(api, GLOBEX_HR, manager_id="u_globex_mgr1")
    assert other_org.status_code == 201, other_org.text
    with unscoped("root read of the hires index definition and both orgs' hires"):
        indexes = root_client[scratch[1]]["new_hires"].index_information()
        hires = list(root_client[scratch[1]]["new_hires"].find({"email_hash": {"$exists": True}}))
    email_index = indexes["org_email_hash_unique"]
    assert email_index["key"] == [("org_id", 1), ("email_hash", 1)]
    assert email_index["unique"] is True
    assert email_index["partialFilterExpression"] == {"email_hash": {"$exists": True}}
    assert sorted(h["org_id"] for h in hires) == ["org_acme", "org_globex"]


def test_every_reveal_lands_in_pii_access_log_and_reads_back(
    api: TestClient, scratch: tuple[str, str], root_client: Client, unscoped: Unscoped
) -> None:
    """Boxes 2 and 3 on Mongo: HR_ADMIN reveals Aadhaar with a reason; the entry is stored in the org
    with a mask, never the value, and `GET /pii-access-log` returns it."""
    hire_id = _create(api).json()["data"]["id"]
    admin = bearer(token_for(*ADMIN))
    revealed = api.post(f"/hires/{hire_id}/pii/aadhaar/reveal", json={"reason": "kyc check"}, headers=admin)
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["data"]["value"] == "999900004202"
    refused = api.post(
        f"/hires/{hire_id}/pii/aadhaar/reveal", json={"reason": "kyc check"}, headers=bearer(token_for(*HR))
    )
    assert refused.status_code == 403
    with unscoped("root read of the access log and audit log as stored"):
        entries = list(root_client[scratch[1]]["pii_access_log"].find({}))
        audit = list(root_client[scratch[1]]["audit_log"].find({"action": "pii.reveal"}))
    assert [(e["org_id"], e["kind"], e["field"], e["masked"], e["reason"]) for e in entries] == [
        ("org_acme", "reveal", "aadhaar", "XXXX XXXX 4202", "kyc check")
    ]
    assert sorted(a["outcome"] for a in audit) == ["denied", "success"]
    assert _leaks(repr(entries) + repr(audit)) == []
    listed = api.get("/pii-access-log", headers=admin).json()
    assert [(e["id"], e["hire_id"]) for e in listed["data"]] == [(revealed.json()["data"]["access_id"], hire_id)]
