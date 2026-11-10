"""`RepositoryOrgKeyStore`: `org_keys` documents through a T-1 `TenantRepository` (PRD §7.9, §7.10)."""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    KeyPurpose,
    KeyUnwrapError,
    KeyVersionConflictError,
    OrgKeyRecord,
    RepositoryOrgKeyStore,
    WrappedKey,
)
from onb_data import TenantRepository

from .fakes import ORG_A, FakeTenantRepository

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def record(version: int = 1, master_key_id: str = "a" * 16) -> OrgKeyRecord:
    wrapped = {p: WrappedKey(bytes(12), bytes([version]) * 48) for p in KeyPurpose}
    return OrgKeyRecord(ORG_A, version, master_key_id, wrapped, NOW)


def store_over(repo: FakeTenantRepository) -> RepositoryOrgKeyStore:
    return RepositoryOrgKeyStore(cast(TenantRepository, repo))


def test_the_collection_is_org_keys() -> None:
    assert ORG_KEYS_COLLECTION == "org_keys"


def test_add_then_load_round_trips_and_sorts_by_version() -> None:
    repo = FakeTenantRepository(ORG_A)
    store = store_over(repo)
    run(store.add(record(2)))
    run(store.add(record(1)))
    loaded = run(store.load(ORG_A))
    assert [r.version for r in loaded] == [1, 2]
    assert loaded[0] == record(1)
    assert repo.last_find[0] == {"org_id": ORG_A}, "the repository checks the org against the tenant context"


def test_the_stored_document_holds_wrapped_keys_only() -> None:
    repo = FakeTenantRepository(ORG_A)
    run(store_over(repo).add(record(1)))
    (doc,) = repo.documents
    assert set(doc) == {"_id", "org_id", "key_version", "master_key_id", "created_at", "keys", "wrap_alg"}
    assert doc["wrap_alg"] == "A256GCM"
    assert set(doc["keys"]) == {p.value for p in KeyPurpose}
    assert set(doc["keys"]["data"]) == {"nonce", "ciphertext"}


def test_a_duplicate_version_is_a_conflict() -> None:
    repo = FakeTenantRepository(ORG_A)
    repo.duplicate_on_insert = True
    with pytest.raises(KeyVersionConflictError):
        run(store_over(repo).add(record(1)))


def test_replace_is_conditional_on_the_expected_master_key() -> None:
    repo = FakeTenantRepository(ORG_A)
    store = store_over(repo)
    assert run(store.replace(record(3, "b" * 16), expected_master_key_id="a" * 16)) is True
    query, update = repo.last_update
    assert query == {"org_id": ORG_A, "key_version": 3, "master_key_id": "a" * 16}
    assert update["$set"]["master_key_id"] == "b" * 16
    assert set(update["$set"]["keys"]) == {p.value for p in KeyPurpose}
    repo.matched = 0
    assert run(store.replace(record(3, "b" * 16), expected_master_key_id="a" * 16)) is False


@pytest.mark.parametrize(
    "corrupt",
    [
        {"key_version": "1"},
        {"key_version": True},
        {"master_key_id": 5},
        {"created_at": "yesterday"},
        {"keys": "nope"},
        {"keys": {"data": {"nonce": b"x" * 12, "ciphertext": b"y" * 48}}},
        {"keys": {p.value: {"nonce": "str", "ciphertext": b"y"} for p in KeyPurpose}},
        {"keys": {**{p.value: {"nonce": b"n", "ciphertext": b"c"} for p in KeyPurpose}, "extra": {}}},
        {"wrap_alg": "A128CBC"},
        {"org_id": "org_beta"},
    ],
)
def test_a_malformed_document_fails_closed(corrupt: dict[str, Any]) -> None:
    repo = FakeTenantRepository(ORG_A)
    run(store_over(repo).add(record(1)))
    repo.documents = [{**repo.documents[0], **corrupt}]
    with pytest.raises(KeyUnwrapError, match=r"malformed|another org"):
        run(store_over(repo).load(ORG_A))
