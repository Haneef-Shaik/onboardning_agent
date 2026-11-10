"""Guards added after the PII-1 code and security reviews: tenant context on every call, stale caches,
load/rotate races, duplicate versions, immutable key maps, `Sealed` validation, safe re-wrapping, and
helpers that pick the right key for PII-2 (PRD §7.9, §7.10)."""

import asyncio
from datetime import UTC, datetime

import pytest
from onb_crypto import (
    BlindIndexField,
    CryptoError,
    KeyPurpose,
    KeyRewrapConflictError,
    Keyring,
    KeyUnwrapError,
    OrgKeyRecord,
    Sealed,
    associated_data,
    blind_index,
)
from onb_data import NoTenantContextError, TenantViolationError, tenant_scope

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, in_org, new_master_key

AD = b"hire_1|mobile|ptk_1|1"


def two_org_keyring() -> tuple[Keyring, InMemoryOrgKeyStore]:
    store = InMemoryOrgKeyStore()
    keyring = Keyring(new_master_key(), store)
    in_org(ORG_A, keyring.create(ORG_A))
    in_org(ORG_B, keyring.create(ORG_B))
    return keyring, store


# --- tenant context: org_id comes from the signed token's context, cached or not --------------------


def test_cached_keys_of_another_org_are_refused_in_this_tenant_context() -> None:
    keyring, _ = two_org_keyring()
    sealed = in_org(ORG_B, keyring.encrypt(ORG_B, b"v", associated_data=AD))  # org B's keys now cached
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, keyring.active(ORG_B))
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, keyring.decrypt(ORG_B, sealed, associated_data=AD))
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, keyring.encrypt(ORG_B, b"v", associated_data=AD))


@pytest.mark.parametrize("method", ["active", "rotate", "verify"])
def test_every_call_needs_a_tenant_context(method: str) -> None:
    keyring, _ = two_org_keyring()
    in_org(ORG_A, keyring.active(ORG_A))  # cached
    with pytest.raises(NoTenantContextError):
        asyncio.run(getattr(keyring, method)(ORG_A))


def test_create_and_rewrap_need_the_matching_tenant_context() -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, keyring.create("org_gamma"))
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, keyring.rewrap(ORG_B, new_master_key()))


# --- stale caches and races -------------------------------------------------------------------------


def test_a_keyring_with_a_stale_cache_reads_data_written_under_a_newer_version() -> None:
    keyring, store = two_org_keyring()
    other_process = Keyring(keyring.master, store)
    assert in_org(ORG_A, other_process.active(ORG_A)).version == 1  # cached at v1
    in_org(ORG_A, keyring.rotate(ORG_A))
    newer = in_org(ORG_A, keyring.encrypt(ORG_A, b"after rotation", associated_data=AD))
    assert newer.key_version == 2
    assert in_org(ORG_A, other_process.decrypt(ORG_A, newer, associated_data=AD)) == b"after rotation"
    assert in_org(ORG_A, other_process.active(ORG_A)).version == 2, "the reload also moves writes forward"


def test_a_load_racing_a_rotation_does_not_cache_the_stale_snapshot() -> None:
    keyring, store = two_org_keyring()
    real_load = store.load
    gate = asyncio.Event()
    calls = 0

    async def slow_first_load(org_id: str) -> list[OrgKeyRecord]:
        nonlocal calls
        calls += 1
        snapshot = await real_load(org_id)
        if calls == 1:
            await gate.wait()
        return snapshot

    store.load = slow_first_load  # type: ignore[method-assign]  # delay the first read past the rotation

    async def race() -> int:
        reader = asyncio.create_task(keyring.active(ORG_A))
        await asyncio.sleep(0)
        await keyring.rotate(ORG_A)
        gate.set()
        assert (await reader).version == 1, "the slow read returns what it read"
        return (await keyring.active(ORG_A)).version

    keyring.forget()
    assert in_org(ORG_A, race()) == 2, "but the stale snapshot was not cached"


def test_duplicate_versions_in_the_store_fail_closed() -> None:
    keyring, store = two_org_keyring()
    record = store.records[(ORG_A, 1)]

    async def duplicated(org_id: str) -> list[OrgKeyRecord]:
        return [record, record]

    store.load = duplicated  # type: ignore[method-assign]  # a store without the unique index
    keyring.forget()
    with pytest.raises(KeyUnwrapError, match="duplicate key version"):
        in_org(ORG_A, keyring.active(ORG_A))


# --- immutability and input validation --------------------------------------------------------------


def test_unwrapped_and_stored_key_maps_are_read_only() -> None:
    keyring, store = two_org_keyring()
    keys = in_org(ORG_A, keyring.active(ORG_A))
    with pytest.raises(TypeError):
        keys.keys[KeyPurpose.DATA] = keys.key(KeyPurpose.SIGNING)  # type: ignore[index]  # must be refused
    with pytest.raises(TypeError):
        store.records[(ORG_A, 1)].wrapped[KeyPurpose.DATA] = store.records[(ORG_A, 1)].wrapped[KeyPurpose.INDEX]  # type: ignore[index]  # must be refused


@pytest.mark.parametrize(
    ("version", "nonce", "ciphertext", "error"),
    [
        (True, b"n" * 12, b"c" * 32, TypeError),
        ("1", b"n" * 12, b"c" * 32, TypeError),
        (0, b"n" * 12, b"c" * 32, ValueError),
        (1, b"n" * 11, b"c" * 32, ValueError),
        (1, "n" * 12, b"c" * 32, TypeError),
        (1, b"n" * 12, "c" * 32, TypeError),
    ],
)
def test_sealed_values_are_validated(version: object, nonce: object, ciphertext: object, error: type) -> None:
    with pytest.raises(error):
        Sealed(version, nonce, ciphertext)  # type: ignore[arg-type]  # the checks under test refuse these


# --- re-wrapping the master key ---------------------------------------------------------------------


def test_rewrap_conflicts_are_crypto_errors() -> None:
    assert issubclass(KeyRewrapConflictError, CryptoError)


def test_rewrap_fails_if_a_version_is_still_on_the_old_master_afterwards() -> None:
    keyring, store = two_org_keyring()

    async def accepted_but_lost(record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
        return True  # the write claims success but nothing changed

    store.replace = accepted_but_lost  # type: ignore[method-assign]  # simulate a lost write
    with pytest.raises(KeyRewrapConflictError, match="still wrapped by another master"):
        in_org(ORG_A, keyring.rewrap(ORG_A, new_master_key()))


def test_rewrap_proves_the_new_wrapping_opens_before_writing_it() -> None:
    keyring, store = two_org_keyring()
    written: list[OrgKeyRecord] = []
    real_replace = store.replace

    async def spy(record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
        written.append(record)
        return await real_replace(record, expected_master_key_id=expected_master_key_id)

    store.replace = spy  # type: ignore[method-assign]  # observe what is written
    new_master = new_master_key()
    in_org(ORG_A, keyring.rewrap(ORG_A, new_master))
    assert [r.master_key_id for r in written] == [new_master.key_id]
    assert in_org(ORG_A, Keyring(new_master, store).verify(ORG_A)) == 1


# --- helpers for PII-2 ------------------------------------------------------------------------------


def test_associated_data_is_length_prefixed_so_parts_cannot_shift() -> None:
    assert associated_data("ab", "c") != associated_data("a", "bc")
    assert associated_data("a|b") != associated_data("a", "b")
    assert associated_data("hire_1", b"mobile", 1) == associated_data("hire_1", "mobile", 1)
    with pytest.raises(TypeError):
        associated_data(1.5)  # type: ignore[arg-type]  # only str, bytes and int are accepted
    with pytest.raises(TypeError):
        associated_data(True)  # bool is refused although it is an int subtype


def test_keyring_blind_index_uses_the_aadhaar_index_key_only_for_aadhaar() -> None:
    keyring, _ = two_org_keyring()
    keys = in_org(ORG_A, keyring.active(ORG_A))
    index, aadhaar_index = keys.key(KeyPurpose.INDEX).reveal(), keys.key(KeyPurpose.AADHAAR_INDEX).reveal()
    aadhaar = "9999 0000 0125"
    assert in_org(ORG_A, keyring.blind_index(ORG_A, BlindIndexField.AADHAAR, aadhaar)) == blind_index(
        aadhaar_index, BlindIndexField.AADHAAR, aadhaar
    )
    assert in_org(ORG_A, keyring.blind_index(ORG_A, BlindIndexField.MOBILE, "9000000001")) == blind_index(
        index, BlindIndexField.MOBILE, "9000000001"
    )
    assert in_org(ORG_A, keyring.blind_index(ORG_A, BlindIndexField.MOBILE, "9000000001")) != in_org(
        ORG_B, keyring.blind_index(ORG_B, BlindIndexField.MOBILE, "9000000001")
    )


def test_blind_indexes_survive_rotation() -> None:
    keyring, _ = two_org_keyring()
    before = in_org(ORG_A, keyring.blind_index(ORG_A, BlindIndexField.EMAIL, "priya@example.com"))
    in_org(ORG_A, keyring.rotate(ORG_A))
    assert in_org(ORG_A, keyring.blind_index(ORG_A, BlindIndexField.EMAIL, "priya@example.com")) == before


def test_created_at_comes_from_the_clock() -> None:
    fixed = datetime(2026, 11, 10, tzinfo=UTC)
    store = InMemoryOrgKeyStore()
    with tenant_scope(ORG_A):
        asyncio.run(Keyring(new_master_key(), store, clock=lambda: fixed).create(ORG_A))
    assert store.records[(ORG_A, 1)].created_at == fixed
