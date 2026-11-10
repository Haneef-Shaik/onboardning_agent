"""The vault on the real compose MongoDB through T-1's `TenantRepository` (PRD §7.9, §7.10).

What lands in `pii_vault`, `pii_vault_aadhaar` and `pii_consent` is read back with the root client; a
tamper is made with the root client, as someone with direct database access would.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import uuid
from collections.abc import Callable, Coroutine, Iterator
from contextlib import AbstractContextManager
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    PURGED,
    AadhaarConsent,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    TokenNotFoundError,
    Vault,
    VaultConflictError,
    VaultField,
    VaultIntegrityError,
    ensure_org_keys_index,
    ensure_vault_indexes,
    is_token,
)
from onb_data import DataStore, connect, tenant_scope
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

Client = MongoClient[dict[str, object]]
# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root action across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]
ORG_A, ORG_B = "org_alpha", "org_beta"
HIRE_1, HIRE_2 = "hire_0001", "hire_0002"
HR = "user_hr_01"
MOBILE_1, MOBILE_2 = "9000000001", "9000000002"
AADHAAR = "999900001231"  # synthetic


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_pii2_{uuid.uuid4().hex[:12]}"
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    yield f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true", name
    root_client.drop_database(name)


def with_vault[T](scratch: tuple[str, str], body: Callable[[Vault], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        store: DataStore = connect(scratch[0], scratch[1])
        try:
            await ensure_org_keys_index(store)
            await ensure_vault_indexes(store)
            master = MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode())
            keyring = Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
            for org in (ORG_A, ORG_B):
                with tenant_scope(org):
                    await keyring.create(org)
            return await body(Vault(keyring, store))
        finally:
            await store.close()

    return asyncio.run(main())


def test_put_update_resolve_and_tamper_against_mongo(
    scratch: tuple[str, str], root_client: Client, unscoped: Unscoped
) -> None:
    db = root_client[scratch[1]]

    async def body(vault: Vault) -> None:
        with tenant_scope(ORG_A):
            old = await vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_1, created_by=HR)
            new = await vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_2, created_by=HR)
            assert is_token(old)
            assert is_token(new)
            assert await vault.resolve(new, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE) == MOBILE_2
            kw: dict[str, Any] = {"org_id": ORG_A, "subject_id": HIRE_1, "field": VaultField.MOBILE}
            assert await vault.resolve(old, allow_superseded=True, **kw) == MOBILE_1

            stored = {d["token"]: d for d in db["pii_vault"].find({"org_id": ORG_A})}
            assert {k: (d["version"], d["status"]) for k, d in stored.items()} == {
                old: (1, "superseded"),
                new: (2, "active"),
            }
            assert all(MOBILE_1 not in repr(d) and MOBILE_2 not in repr(d) for d in stored.values())

            with unscoped("deliberate tamper as root: re-point the token to another hire"):
                db["pii_vault"].update_one({"token": new}, {"$set": {"subject.id": HIRE_2}})
            with pytest.raises(VaultIntegrityError):
                await vault.resolve(new, org_id=ORG_A, subject_id=HIRE_2, field=VaultField.MOBILE)

            await vault.purge(old, **kw)
            assert await vault.resolve(old, **kw) == PURGED
            with unscoped("root read-back by token proves the purge wiped the ciphertext"):
                purged = db["pii_vault"].find_one({"token": old}) or {}
            assert (purged["status"], purged["ciphertext"], purged["nonce"]) == ("purged", b"", b"")

        with tenant_scope(ORG_B), pytest.raises(TokenNotFoundError):
            await vault.resolve(old, org_id=ORG_B, subject_id=HIRE_1, field=VaultField.MOBILE)

    with_vault(scratch, body)


def test_aadhaar_lands_only_in_its_partition_with_consent(
    scratch: tuple[str, str], root_client: Client, unscoped: Unscoped
) -> None:
    db = root_client[scratch[1]]

    async def body(vault: Vault) -> None:
        with tenant_scope(ORG_A):
            token = await vault.put(
                ORG_A,
                subject_id=HIRE_1,
                field=VaultField.AADHAAR,
                value=AADHAAR,
                created_by=HR,
                consent=AadhaarConsent(purpose="employment onboarding KYC"),
            )
            assert await vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.AADHAAR) == AADHAAR
        with unscoped("root read-back of every org's vault records proves where the Aadhaar landed"):
            aadhaar_tokens = [d["token"] for d in db["pii_vault_aadhaar"].find()]
            general_count = db["pii_vault"].count_documents({})
            consent = db["pii_consent"].find_one({"token": token}) or {}
        assert aadhaar_tokens == [token]
        assert general_count == 0
        assert (consent["org_id"], consent["field"], consent["purpose"]) == (
            ORG_A,
            "aadhaar",
            "employment onboarding KYC",
        )
        for name in db.list_collection_names():
            if name != "pii_vault_aadhaar":
                with unscoped("root scan of every collection proves the raw Aadhaar is nowhere else"):
                    others = list(db[name].find())
                assert all(AADHAAR not in repr(d) for d in others)

    with_vault(scratch, body)


def test_the_unique_indexes_refuse_a_second_record_of_the_same_version(
    scratch: tuple[str, str], root_client: Client
) -> None:
    db = root_client[scratch[1]]

    async def body(vault: Vault) -> None:
        with tenant_scope(ORG_A):
            await vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_1, created_by=HR)
        first = db["pii_vault"].find_one({"org_id": ORG_A}) or {}
        copy = {k: v for k, v in first.items() if k != "_id"}
        with pytest.raises(DuplicateKeyError):  # same version under a fresh token
            db["pii_vault"].insert_one({**copy, "token": "ptk_" + "0" * 52})
        with pytest.raises(DuplicateKeyError):  # same token under another version
            db["pii_vault"].insert_one({**copy, "version": 9})
        db["pii_vault"].insert_one({**copy, "org_id": ORG_B})  # unique per org only

    with_vault(scratch, body)


def test_a_concurrent_update_of_one_field_has_one_winner(scratch: tuple[str, str], root_client: Client) -> None:
    async def body(vault: Vault) -> list[object]:
        with tenant_scope(ORG_A):
            await vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=MOBILE_1, created_by=HR)
            writes = [
                vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=v, created_by=HR)
                for v in (MOBILE_2, MOBILE_1, MOBILE_2, MOBILE_1)
            ]
            return list(await asyncio.gather(*writes, return_exceptions=True))

    outcomes = with_vault(scratch, body)
    assert any(isinstance(o, str) for o in outcomes)
    failures = [o for o in outcomes if not isinstance(o, str)]
    assert all(isinstance(o, VaultConflictError) for o in failures), failures
    db = root_client[scratch[1]]
    assert db["pii_vault"].count_documents({"org_id": ORG_A, "status": "active"}) == 1
