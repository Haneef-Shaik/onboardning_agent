"""Signed grants resolving at a sink on the real compose MongoDB, through T-1's `TenantRepository`
(PRD §7.9, §7.10). What lands in `pii_access_log` and `pii_grant_jti` is read back with the root client.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    GrantAuditRecord,
    GrantIssuer,
    GrantRejected,
    GrantSigningKey,
    GrantVerifier,
    Keyring,
    MasterKey,
    RejectReason,
    RepositoryOrgKeyStore,
    SinkResolver,
    SinkToken,
    Vault,
    VaultField,
    ensure_grant_indexes,
    ensure_org_keys_index,
    ensure_vault_indexes,
    load_sink_policy,
)
from onb_data import DataStore, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, object]]
ORG_A, ORG_B = "org_alpha", "org_beta"
HIRE_1, HIRE_2 = "hire_0001", "hire_0002"
HR = "user_hr_01"
MOBILE_1, MOBILE_2 = "9000000001", "9000000002"
TELEGRAM = "notify.telegram"
POLICY = load_sink_policy(Path(__file__).resolve().parents[3] / "config" / "pii_sink_policy.yaml")


class Audit:
    def __init__(self) -> None:
        self.records: list[GrantAuditRecord] = []

    async def record(self, record: GrantAuditRecord) -> None:
        self.records = [*self.records, record]


@dataclass
class Setup:
    vault: Vault
    issuer: GrantIssuer
    resolver: SinkResolver
    audit: Audit


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_pii4_{uuid.uuid4().hex[:12]}"
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    yield f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true", name
    root_client.drop_database(name)


def with_sink[T](scratch: tuple[str, str], body: Callable[[Setup], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        store: DataStore = connect(scratch[0], scratch[1])
        try:
            await ensure_org_keys_index(store)
            await ensure_vault_indexes(store)
            await ensure_grant_indexes(store)
            master = MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode())
            keyring = Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
            for org in (ORG_A, ORG_B):
                with tenant_scope(org):
                    await keyring.create(org)
            key = GrantSigningKey.generate()
            audit = Audit()
            resolver = SinkResolver.create(keyring, store, GrantVerifier([key.public_key]), POLICY, audit)
            return await body(Setup(Vault(keyring, store), GrantIssuer(key, POLICY), resolver, audit))
        finally:
            await store.close()

    return asyncio.run(main())


def grant_for(setup: Setup, org: str = ORG_A, subject: str = HIRE_1) -> str:
    return setup.issuer.issue_grant(
        org, run_id="run_0001", task_id="task_welcome", subject_id=subject, fields=[VaultField.MOBILE], sink=TELEGRAM
    )


def test_a_resolution_writes_one_masked_access_log_entry_in_mongo(
    scratch: tuple[str, str], root_client: Client
) -> None:
    db = root_client[scratch[1]]

    async def body(s: Setup) -> None:
        with tenant_scope(ORG_A):
            token = await s.vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_1, created_by=HR)
            grant = grant_for(s)
            assert await s.resolver.resolve_at_sink(grant, TELEGRAM, [SinkToken(VaultField.MOBILE, token)]) == {
                token: MOBILE_1
            }
            with pytest.raises(GrantRejected) as caught:
                await s.resolver.resolve_at_sink(grant, TELEGRAM, [SinkToken(VaultField.MOBILE, token)])
            assert caught.value.reason is RejectReason.REPLAYED

        entries = list(db["pii_access_log"].find())
        assert len(entries) == 1
        assert entries[0]["org_id"] == ORG_A
        assert entries[0]["masked"] == "90XXXXXX01"
        assert MOBILE_1 not in json.dumps(entries[0], default=str)
        jtis = list(db["pii_grant_jti"].find())
        assert len(jtis) == 1
        assert jtis[0]["org_id"] == ORG_A
        expires_at = jtis[0]["expires_at"]
        assert isinstance(expires_at, datetime)
        assert expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
        assert [r.reason for r in s.audit.records] == [RejectReason.REPLAYED]

    with_sink(scratch, body)


def test_concurrent_uses_of_one_grant_have_exactly_one_winner(scratch: tuple[str, str], root_client: Client) -> None:
    db = root_client[scratch[1]]

    async def body(s: Setup) -> None:
        with tenant_scope(ORG_A):
            token = await s.vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_1, created_by=HR)
            grant = grant_for(s)
            tokens = [SinkToken(VaultField.MOBILE, token)]
            outcomes = await asyncio.gather(
                *(s.resolver.resolve_at_sink(grant, TELEGRAM, tokens) for _ in range(6)), return_exceptions=True
            )
        wins = [o for o in outcomes if isinstance(o, dict)]
        losses = [o for o in outcomes if isinstance(o, GrantRejected)]
        assert len(wins) == 1
        assert len(losses) == 5
        assert {o.reason for o in losses} == {RejectReason.REPLAYED}
        assert db["pii_access_log"].count_documents({}) == 1

    with_sink(scratch, body)


def test_another_hires_and_another_orgs_tokens_are_refused_in_mongo(
    scratch: tuple[str, str], root_client: Client
) -> None:
    db = root_client[scratch[1]]

    async def body(s: Setup) -> None:
        with tenant_scope(ORG_B):
            foreign = await s.vault.put(
                ORG_B, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_2, created_by=HR
            )
        with tenant_scope(ORG_A):
            other = await s.vault.put(ORG_A, subject_id=HIRE_2, field=VaultField.MOBILE, value=MOBILE_2, created_by=HR)
            for token, reason in ((other, RejectReason.NOT_BOUND), (foreign, RejectReason.NOT_FOUND)):
                with pytest.raises(GrantRejected) as caught:
                    await s.resolver.resolve_at_sink(grant_for(s), TELEGRAM, [SinkToken(VaultField.MOBILE, token)])
                assert caught.value.reason is reason
        assert db["pii_access_log"].count_documents({}) == 0

    with_sink(scratch, body)


def test_the_grant_indexes_exist_and_lead_with_org_id(scratch: tuple[str, str], root_client: Client) -> None:
    db = root_client[scratch[1]]

    async def body(_: Setup) -> None:
        return None

    with_sink(scratch, body)
    jti_indexes = {tuple(i["key"]): i.get("unique", False) for i in db["pii_grant_jti"].list_indexes()}
    assert jti_indexes[("org_id", "jti")] is True
    log_indexes = [tuple(i["key"]) for i in db["pii_access_log"].list_indexes()]
    assert ("org_id", "subject.id", "at") in log_indexes
