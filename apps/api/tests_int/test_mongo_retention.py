"""The PII retention purge against a real MongoDB (PII-5 boxes 1-3; PRD FR-P6, §7.10 Retention).

A scratch database of the compose Mongo, dropped afterwards. The job runs on a fake clock; every read-back
uses the root client inside `unscoped(...)`, so nothing trusts the job to report on itself. All values are
synthetic (the `90000` mobile range, PII-3's `9999…` Aadhaar range).
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_agents.runs import RUNS
from onb_api.retention import PiiRetentionJob
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    PURGED,
    AadhaarConsent,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    Vault,
    VaultField,
    ensure_vault_indexes,
)
from onb_data import AuditLog, AuditOutcome, AuditQuery, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]

ORG = "org_acme"
HIRE = "h_ret_0001"
COMPLETED_AT = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
MOBILE = "9000000042"
AADHAAR = "999900004202"


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_pii5_{uuid.uuid4().hex[:12]}"
    yield _uri(root_client), name
    root_client.drop_database(name)


async def _scenario(uri: str, database: str, clock: list[datetime]) -> dict[str, Any]:
    store = connect(uri, database)
    master = MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    keyring = Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    vault = Vault(keyring, store, clock=lambda: clock[0])
    audit = AuditLog(store)
    await store.shared("orgs").insert_one({"_id": ORG})
    with tenant_scope(ORG):
        await keyring.create(ORG)
        await ensure_vault_indexes(store)
        old = await vault.put(ORG, subject_id=HIRE, field=VaultField.MOBILE, value="9000000000", created_by="u_hr")
        mobile = await vault.put(ORG, subject_id=HIRE, field=VaultField.MOBILE, value=MOBILE, created_by="u_hr")
        aadhaar = await vault.put(
            ORG,
            subject_id=HIRE,
            field=VaultField.AADHAAR,
            value=AADHAAR,
            created_by="u_hr",
            consent=AadhaarConsent(purpose="statutory onboarding records"),
        )
        await store.tenant("new_hires").insert_one(
            {
                "_id": HIRE,
                "name": "Test Hire",
                "pii": {"mobile": mobile, "aadhaar": aadhaar},
                "pii_masked": {"mobile": "******0042", "aadhaar": "XXXX XXXX 4202"},
                "pii_versions": {"mobile": 2, "aadhaar": 1},
                "mobile_hash": "a" * 64,
                "aadhaar_hash": "b" * 64,
                "email_hash": "c" * 64,
            }
        )
        history = [{"from": "running", "to": "completed", "at": COMPLETED_AT, "by": None}]
        await store.tenant(RUNS).insert_one(
            {"_id": "run_ret_1", "hire_id": HIRE, "status": "completed", "dag_hash": "d" * 64, "history": history}
        )
    job = PiiRetentionJob(store, vault, audit, clock=lambda: clock[0])
    outcome: dict[str, Any] = {"tokens": [old, mobile, aadhaar]}
    clock[0] = COMPLETED_AT + timedelta(days=89, hours=23)
    outcome["day89"] = await job.run_once()
    with tenant_scope(ORG):
        outcome["day89_value"] = await vault.resolve(mobile, org_id=ORG, subject_id=HIRE, field=VaultField.MOBILE)
    clock[0] = COMPLETED_AT + timedelta(days=90)
    outcome["day90"] = await job.run_once()
    with tenant_scope(ORG):
        outcome["day90_value"] = await vault.resolve(mobile, org_id=ORG, subject_id=HIRE, field=VaultField.MOBILE)
    clock[0] = COMPLETED_AT + timedelta(days=91)
    outcome["again"] = await job.run_once()
    with tenant_scope(ORG):
        page = await audit.list(AuditQuery(action="pii.purge", outcome=AuditOutcome.SUCCESS), page=1, limit=50)
    outcome["audit_targets"] = sorted(entry.target for entry in page.entries)
    return outcome


def test_the_job_purges_on_day_90_keeps_the_consent_audits_each_value_and_is_idempotent(
    scratch: tuple[str, str], root_client: Client, unscoped: Unscoped
) -> None:
    uri, database = scratch
    clock = [COMPLETED_AT]
    outcome = asyncio.run(_scenario(uri, database, clock))
    assert outcome["day89"].hires_purged == 0
    assert outcome["day89_value"] == MOBILE
    assert (outcome["day90"].hires_purged, outcome["day90"].values_purged) == (1, 3)
    assert outcome["day90_value"] == PURGED
    assert (outcome["again"].hires_purged, outcome["again"].values_purged, outcome["again"].failures) == (0, 0, 0)
    assert outcome["audit_targets"] == sorted(f"pii:{token}" for token in outcome["tokens"])
    with unscoped("root read-back of the vault, consent and hire after the purge"):
        db = root_client[database]
        records = list(db["pii_vault"].find({})) + list(db["pii_vault_aadhaar"].find({}))
        consent = list(db["pii_consent"].find({}))
        hire = db["new_hires"].find_one({"_id": HIRE})
    assert len(records) == 3
    assert all(r["status"] == "purged" and r["ciphertext"] == b"" and r["nonce"] == b"" for r in records)
    assert [c["purpose"] for c in consent] == ["statutory onboarding records"]
    assert hire is not None
    assert hire["pii_masked"] == {"mobile": PURGED, "aadhaar": PURGED}
    assert not {"pii_versions", "mobile_hash", "aadhaar_hash", "email_hash"} & set(hire)
