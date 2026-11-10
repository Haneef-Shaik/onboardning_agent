"""Regression tests for the PII-2 code and security review: replayed old versions, a half-finished
update, consent left behind, and error classes for races versus tampering."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_crypto import (
    AadhaarConsent,
    SupersededTokenError,
    Vault,
    VaultConflictError,
    VaultField,
    VaultIntegrityError,
    ensure_vault_indexes,
)

from .fakes import ORG_A, in_org
from .vault_fakes import FakeVaultStore, keyring_for_two_orgs

HIRE_1 = "hire_0001"
HR = "user_hr_01"
MOBILE_1, MOBILE_2, MOBILE_3 = "9000000001", "9000000002", "9000000003"
NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)


def make_vault() -> tuple[Vault, FakeVaultStore]:
    store = FakeVaultStore()
    return Vault(keyring_for_two_orgs(), store, clock=lambda: NOW), store


def put(vault: Vault, value: str) -> str:
    return in_org(ORG_A, vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value=value, created_by=HR))


def resolve(vault: Vault, token: str, **kw: Any) -> str:
    return in_org(ORG_A, vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, **kw))


def statuses(store: FakeVaultStore) -> dict[int, str]:
    return {d["version"]: d["status"] for d in store.tenant("pii_vault").documents}


# --- security review H1: an old signed version written back is not live again -------------------------


def test_a_replayed_active_copy_of_a_superseded_record_does_not_resolve() -> None:
    vault, store = make_vault()
    old = put(vault, MOBILE_1)
    backup = copy.deepcopy(store.tenant("pii_vault").raw(old))  # taken while v1 was active
    put(vault, MOBILE_2)
    stored = store.tenant("pii_vault").raw(old)
    stored.clear()
    stored.update(backup)  # write the old, validly signed "active" document back
    assert statuses(store) == {1: "active", 2: "active"}
    with pytest.raises(SupersededTokenError):
        resolve(vault, old)
    assert resolve(vault, old, allow_superseded=True) == MOBILE_1, "still the audit value, nothing more"


# --- code review H1 / security review M3: a half-finished update ---------------------------------------


def test_a_failed_supersede_leaves_no_live_old_value_and_the_next_update_cleans_up() -> None:
    vault, store = make_vault()
    old = put(vault, MOBILE_1)
    store.tenant("pii_vault").fail_next_update = True
    with pytest.raises(VaultConflictError, match="superseded"):
        put(vault, MOBILE_2)
    assert statuses(store) == {1: "active", 2: "active"}
    with pytest.raises(SupersededTokenError):
        resolve(vault, old)
    newest = put(vault, MOBILE_3)
    assert statuses(store) == {1: "superseded", 2: "superseded", 3: "active"}
    assert resolve(vault, newest) == MOBILE_3


def test_an_old_record_purged_during_an_update_does_not_fail_the_update() -> None:
    vault, store = make_vault()
    old = put(vault, MOBILE_1)
    repo = store.tenant("pii_vault")
    real_update = repo.update_one

    async def purge_first(query: Any, update: Any, **kw: Any) -> Any:
        repo.update_one = real_update  # type: ignore[method-assign]  # one-shot interception
        await vault.purge(old, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE)
        return await real_update(query, update, **kw)

    repo.update_one = purge_first  # type: ignore[method-assign]  # a purge job wins the race
    new = put(vault, MOBILE_2)
    assert statuses(store) == {1: "purged", 2: "active"}
    assert resolve(vault, new) == MOBILE_2


def test_an_update_refuses_to_build_on_a_newest_record_without_a_token() -> None:
    vault, store = make_vault()
    old = put(vault, MOBILE_1)
    del store.tenant("pii_vault").raw(old)["token"]
    with pytest.raises(VaultIntegrityError):
        put(vault, MOBILE_2)


# --- error classes: races are conflicts, tampering is integrity ----------------------------------------


def test_a_purge_that_loses_a_race_is_a_conflict() -> None:
    vault, store = make_vault()
    token = put(vault, MOBILE_1)
    store.tenant("pii_vault").fail_next_update = True
    with pytest.raises(VaultConflictError, match="purge"):
        in_org(ORG_A, vault.purge(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))


# --- consent is not left behind ------------------------------------------------------------------------


def test_a_failed_aadhaar_write_leaves_no_consent_record() -> None:
    vault, store = make_vault()
    repo = store.tenant("pii_vault_aadhaar")

    async def refuse(document: Any) -> Any:
        from pymongo.errors import DuplicateKeyError

        raise DuplicateKeyError("E11000 duplicate key")

    repo.insert_one = refuse  # type: ignore[method-assign]  # a concurrent writer won
    with pytest.raises(VaultConflictError):
        in_org(
            ORG_A,
            vault.put(
                ORG_A,
                subject_id=HIRE_1,
                field=VaultField.AADHAAR,
                value="999900001231",
                created_by=HR,
                consent=AadhaarConsent(purpose="employment onboarding KYC"),
            ),
        )
    assert store.tenant("pii_consent").documents == []


# --- the indexes -----------------------------------------------------------------------------------------


class _IndexRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[str, int]], bool]] = []

    async def ensure_index(self, collection: str, keys: list[tuple[str, int]], *, unique: bool = False) -> str:
        self.calls = [*self.calls, (collection, keys, unique)]
        return "ok"


def test_the_vault_indexes_make_tokens_and_versions_unique_per_org_in_both_partitions() -> None:
    recorder = _IndexRecorder()
    asyncio.run(ensure_vault_indexes(recorder))  # type: ignore[arg-type]  # records calls only
    for partition in ("pii_vault", "pii_vault_aadhaar"):
        assert (partition, [("org_id", 1), ("token", 1)], True) in recorder.calls
        assert (partition, [("org_id", 1), ("subject.id", 1), ("field", 1), ("version", 1)], True) in recorder.calls
    assert ("pii_consent", [("org_id", 1), ("subject.id", 1)], False) in recorder.calls
