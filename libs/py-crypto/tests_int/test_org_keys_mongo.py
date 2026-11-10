"""Org keys in the real `org_keys` collection (compose MongoDB, PRD §7.9, §7.10, §15).

Two orgs, keys stored through T-1's `TenantRepository`; isolation, a wrong master key, both runbook
procedures, and what actually lands in the database, read back with the root client.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import uuid
from collections.abc import Callable, Coroutine, Iterator
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    DecryptionError,
    KeyPurpose,
    Keyring,
    KeyUnwrapError,
    KeyVersionConflictError,
    MasterKey,
    RepositoryOrgKeyStore,
    ensure_org_keys_index,
    rewrap_orgs,
    rotate_orgs,
    verify_orgs,
)
from onb_data import DataStore, NoTenantContextError, TenantViolationError, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, object]]
ORG_A, ORG_B = "org_alpha", "org_beta"
ORGS = [ORG_A, ORG_B]
AD = b"hire_1|mobile|ptk_1|1"


def new_master() -> MasterKey:
    return MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode())


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_pii1_{uuid.uuid4().hex[:12]}"
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    yield f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true", name
    root_client.drop_database(name)


def with_store[T](scratch: tuple[str, str], body: Callable[[DataStore], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        store = connect(scratch[0], scratch[1])
        try:
            await ensure_org_keys_index(store)
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(main())


def keyring_for(store: DataStore, master: MasterKey) -> Keyring:
    return Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))


async def provision(keyring: Keyring) -> None:
    for org in ORGS:
        with tenant_scope(org):
            await keyring.create(org)


def test_two_orgs_keys_are_isolated_and_a_wrong_master_fails_closed(
    scratch: tuple[str, str], root_client: Client
) -> None:
    master = new_master()

    async def body(store: DataStore) -> None:
        keyring = keyring_for(store, master)
        await provision(keyring)
        with tenant_scope(ORG_B):
            sealed = await keyring.encrypt(ORG_B, b"9000000001", associated_data=AD)
            assert await keyring.decrypt(ORG_B, sealed, associated_data=AD) == b"9000000001"
        with tenant_scope(ORG_A), pytest.raises(DecryptionError):
            await keyring.decrypt(ORG_A, sealed, associated_data=AD)
        # Org A's context cannot even load org B's key records.
        keyring.forget()
        with tenant_scope(ORG_A), pytest.raises(TenantViolationError):
            await keyring.active(ORG_B)
        with pytest.raises(NoTenantContextError):
            await keyring.active(ORG_A)
        with tenant_scope(ORG_B), pytest.raises(KeyUnwrapError):
            await keyring_for(store, new_master()).decrypt(ORG_B, sealed, associated_data=AD)
        with tenant_scope(ORG_A), pytest.raises(KeyVersionConflictError):
            await keyring.create(ORG_A)

    with_store(scratch, body)

    docs = list(root_client[scratch[1]][ORG_KEYS_COLLECTION].find({}))
    assert sorted((d["org_id"], d["key_version"]) for d in docs) == [(ORG_A, 1), (ORG_B, 1)]
    for doc in docs:
        keys = doc["keys"]
        assert isinstance(keys, dict)
        assert set(keys) == {p.value for p in KeyPurpose}
        assert all(isinstance(w["ciphertext"], bytes) and len(w["ciphertext"]) == 48 for w in keys.values())
        assert doc["master_key_id"] == master.key_id


def test_the_runbook_procedures_against_mongo(scratch: tuple[str, str], root_client: Client) -> None:
    old_master, next_master = new_master(), new_master()

    async def body(store: DataStore) -> None:
        keyring = keyring_for(store, old_master)
        await provision(keyring)
        before = {}
        for org in ORGS:
            with tenant_scope(org):
                before[org] = await keyring.encrypt(org, org.encode(), associated_data=AD)

        # Procedure A
        assert await verify_orgs(keyring, ORGS) == {ORG_A: 1, ORG_B: 1}  # A1.
        assert await rotate_orgs(keyring, ORGS) == {ORG_A: 2, ORG_B: 2}  # A2.
        restarted = keyring_for(store, old_master)  # A3.
        assert await verify_orgs(restarted, ORGS) == {ORG_A: 2, ORG_B: 2}  # A4.
        for org in ORGS:
            with tenant_scope(org):
                assert (await restarted.encrypt(org, b"x", associated_data=AD)).key_version == 2
                assert await restarted.decrypt(org, before[org], associated_data=AD) == org.encode()

        # Procedure B: B1 new key, B2 writers stopped (nothing else runs here).
        assert await rewrap_orgs(keyring, next_master, ORGS) == {ORG_A: 2, ORG_B: 2}  # B3.
        assert await rewrap_orgs(keyring, next_master, ORGS) == {ORG_A: 0, ORG_B: 0}
        after = keyring_for(store, next_master)
        assert await verify_orgs(after, ORGS) == {ORG_A: 2, ORG_B: 2}  # B4.
        for org in ORGS:
            with tenant_scope(org):
                assert await after.decrypt(org, before[org], associated_data=AD) == org.encode()
        with pytest.raises(KeyUnwrapError):  # B5.
            await verify_orgs(keyring_for(store, old_master), ORGS)

    with_store(scratch, body)

    docs = list(root_client[scratch[1]][ORG_KEYS_COLLECTION].find({}))
    assert len(docs) == 4, "A5: old versions are kept"
    assert {d["master_key_id"] for d in docs} == {next_master.key_id}
    indexes = root_client[scratch[1]][ORG_KEYS_COLLECTION].index_information()
    assert any(i.get("unique") and i["key"] == [("org_id", 1), ("key_version", 1)] for i in indexes.values())
