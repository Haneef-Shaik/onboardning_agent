"""The per-org keyring: isolation between orgs, fail-closed unwrapping, versioned rotation (PRD §7.10)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from onb_crypto import (
    DecryptionError,
    KeyPurpose,
    Keyring,
    KeyUnwrapError,
    KeyVersionConflictError,
    NoOrgKeysError,
    OrgKeyRecord,
    Sealed,
    UnknownKeyVersionError,
)

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, new_master_key

AD = b"hire_1|mobile|ptk_1|1"
SECRET = b"9000000001"


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def two_org_keyring() -> tuple[Keyring, InMemoryOrgKeyStore]:
    store = InMemoryOrgKeyStore()
    keyring = Keyring(new_master_key(), store)
    run(keyring.create(ORG_A))
    run(keyring.create(ORG_B))
    return keyring, store


# --- Box 1: org A's key cannot decrypt org B's data -------------------------------------------------


def test_each_org_gets_its_own_distinct_keys_for_every_purpose() -> None:
    keyring, _ = two_org_keyring()
    a, b = run(keyring.active(ORG_A)), run(keyring.active(ORG_B))
    assert a.version == b.version == 1
    material = [k.key(p).reveal() for k in (a, b) for p in KeyPurpose]
    assert len(set(material)) == len(material), "every org and purpose has its own key"


def test_an_org_decrypts_its_own_data() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_B, SECRET, associated_data=AD))
    assert sealed.key_version == 1
    assert SECRET not in sealed.ciphertext
    assert run(keyring.decrypt(ORG_B, sealed, associated_data=AD)) == SECRET


def test_org_a_cannot_decrypt_org_b_data() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_B, SECRET, associated_data=AD))
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_A, sealed, associated_data=AD))


def test_org_a_key_material_cannot_open_org_b_ciphertext_even_with_org_b_binding() -> None:
    """Not only the org_id in the associated data: org A's actual key fails on org B's ciphertext."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_B, SECRET, associated_data=AD))
    a_key = run(keyring.active(ORG_A)).key(KeyPurpose.DATA).reveal()
    with pytest.raises(InvalidTag):
        AESGCM(a_key).decrypt(sealed.nonce, sealed.ciphertext, keyring.bound_data(ORG_B, KeyPurpose.DATA, 1, AD))


def test_org_b_aadhaar_data_is_not_readable_by_org_a_or_with_the_data_key() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_B, b"234123412346", associated_data=AD, purpose=KeyPurpose.AADHAAR))
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_A, sealed, associated_data=AD, purpose=KeyPurpose.AADHAAR))
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_B, sealed, associated_data=AD, purpose=KeyPurpose.DATA))
    assert run(keyring.decrypt(ORG_B, sealed, associated_data=AD, purpose=KeyPurpose.AADHAAR)) == b"234123412346"


def test_ciphertext_moved_to_other_associated_data_fails() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_A, sealed, associated_data=b"hire_2|mobile|ptk_1|1"))


def test_tampered_ciphertext_fails_closed() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    tampered = Sealed(sealed.key_version, sealed.nonce, bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:])
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_A, tampered, associated_data=AD))


@pytest.mark.parametrize("purpose", [KeyPurpose.SIGNING, KeyPurpose.INDEX, KeyPurpose.AADHAAR_INDEX])
def test_only_encryption_purposes_encrypt(purpose: KeyPurpose) -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(ValueError, match="not an encryption key"):
        run(keyring.encrypt(ORG_A, SECRET, associated_data=AD, purpose=purpose))
    with pytest.raises(ValueError, match="not an encryption key"):
        run(keyring.decrypt(ORG_A, Sealed(1, b"n" * 12, b"c" * 32), associated_data=AD, purpose=purpose))


def test_the_store_never_holds_unwrapped_key_material() -> None:
    keyring, store = two_org_keyring()
    keys = run(keyring.active(ORG_A))
    stored = repr(store.records) + "".join(
        w.nonce.hex() + w.ciphertext.hex() for r in store.records.values() for w in r.wrapped.values()
    )
    for purpose in KeyPurpose:
        assert keys.key(purpose).reveal().hex() not in stored


# --- Box 1: a wrong master key fails closed ---------------------------------------------------------


def test_a_wrong_master_key_fails_closed_and_returns_nothing() -> None:
    keyring, store = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    impostor = Keyring(new_master_key(), store)
    with pytest.raises(KeyUnwrapError, match="different master key"):
        run(impostor.active(ORG_A))
    with pytest.raises(KeyUnwrapError):
        run(impostor.decrypt(ORG_A, sealed, associated_data=AD))
    with pytest.raises(KeyUnwrapError):
        run(impostor.encrypt(ORG_A, SECRET, associated_data=AD))


def test_a_wrong_master_key_fails_even_when_the_record_claims_its_id() -> None:
    """The master key id is a hint for operators; the AES-GCM tag is the guard."""
    _, store = two_org_keyring()
    impostor_master = new_master_key()
    store.records = {
        k: OrgKeyRecord(r.org_id, r.version, impostor_master.key_id, r.wrapped, r.created_at)
        for k, r in store.records.items()
    }
    with pytest.raises(KeyUnwrapError, match="cannot be unwrapped"):
        run(Keyring(impostor_master, store).active(ORG_A))


def test_a_key_record_swapped_from_another_org_fails_closed() -> None:
    keyring, store = two_org_keyring()
    b_record = store.records[(ORG_B, 1)]
    store.records = {
        **store.records,
        (ORG_A, 1): OrgKeyRecord(ORG_A, 1, b_record.master_key_id, b_record.wrapped, b_record.created_at),
    }
    with pytest.raises(KeyUnwrapError):
        run(Keyring(keyring.master, store).active(ORG_A))


def test_an_org_without_keys_is_refused() -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(NoOrgKeysError):
        run(keyring.active("org_gamma"))
    with pytest.raises(NoOrgKeysError):
        run(keyring.encrypt("org_gamma", SECRET, associated_data=AD))


def test_creating_keys_twice_is_refused() -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(KeyVersionConflictError):
        run(keyring.create(ORG_A))


@pytest.mark.parametrize("org_id", ["", "org a", "org|a", "x" * 129, 7])
def test_invalid_org_ids_are_refused(org_id: object) -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(ValueError, match="org id"):
        run(keyring.active(org_id))  # type: ignore[arg-type]  # the check under test refuses non-str


# --- Box 2: rotation ---------------------------------------------------------------------------------


def test_after_rotation_old_ciphertext_decrypts_and_new_writes_use_the_new_version() -> None:
    keyring, store = two_org_keyring()
    old = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    assert old.key_version == 1

    assert run(keyring.rotate(ORG_A)) == 2

    new = run(keyring.encrypt(ORG_A, b"9000000002", associated_data=AD))
    assert new.key_version == 2
    assert run(keyring.decrypt(ORG_A, old, associated_data=AD)) == SECRET
    assert run(keyring.decrypt(ORG_A, new, associated_data=AD)) == b"9000000002"
    assert sorted(v for (org, v) in store.records if org == ORG_A) == [1, 2]

    # A fresh process (empty cache) sees the same: the rotation is in the store, not only in memory.
    fresh = Keyring(keyring.master, store)
    assert run(fresh.active(ORG_A)).version == 2
    assert run(fresh.decrypt(ORG_A, old, associated_data=AD)) == SECRET


def test_rotation_replaces_encryption_and_signing_keys_and_keeps_index_keys() -> None:
    keyring, _ = two_org_keyring()
    v1 = run(keyring.active(ORG_A))
    run(keyring.rotate(ORG_A))
    v2 = run(keyring.active(ORG_A))
    for purpose in (KeyPurpose.DATA, KeyPurpose.SIGNING, KeyPurpose.AADHAAR):
        assert v2.key(purpose) != v1.key(purpose)
    for purpose in (KeyPurpose.INDEX, KeyPurpose.AADHAAR_INDEX):
        assert v2.key(purpose) == v1.key(purpose), "stored blind indexes must keep matching"
    assert run(keyring.version(ORG_A, 1)).key(KeyPurpose.DATA) == v1.key(KeyPurpose.DATA)


def test_rotation_of_one_org_leaves_the_other_alone() -> None:
    keyring, _ = two_org_keyring()
    b_before = run(keyring.active(ORG_B))
    run(keyring.rotate(ORG_A))
    assert run(keyring.active(ORG_B)).version == 1
    assert run(keyring.active(ORG_B)).key(KeyPurpose.DATA) == b_before.key(KeyPurpose.DATA)


def test_rotation_seen_by_another_keyring_instance_after_forget() -> None:
    keyring, store = two_org_keyring()
    other = Keyring(keyring.master, store)
    assert run(other.active(ORG_A)).version == 1
    run(keyring.rotate(ORG_A))
    assert run(other.active(ORG_A)).version == 1, "cached until forgotten"
    other.forget(ORG_A)
    assert run(other.active(ORG_A)).version == 2
    other.forget()
    assert run(other.active(ORG_B)).version == 1


def test_ciphertext_naming_an_unknown_version_fails_closed() -> None:
    keyring, _ = two_org_keyring()
    sealed = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    with pytest.raises(UnknownKeyVersionError):
        run(keyring.decrypt(ORG_A, Sealed(9, sealed.nonce, sealed.ciphertext), associated_data=AD))


def test_ciphertext_relabelled_with_another_version_fails() -> None:
    keyring, _ = two_org_keyring()
    run(keyring.rotate(ORG_A))
    sealed = run(keyring.encrypt(ORG_A, SECRET, associated_data=AD))
    with pytest.raises(DecryptionError):
        run(keyring.decrypt(ORG_A, Sealed(1, sealed.nonce, sealed.ciphertext), associated_data=AD))


def test_rotating_an_org_without_keys_is_refused() -> None:
    keyring, _ = two_org_keyring()
    with pytest.raises(NoOrgKeysError):
        run(keyring.rotate("org_gamma"))


def test_a_concurrent_rotation_conflict_surfaces_and_the_winner_stands() -> None:
    """Two rotations read version 1; the store's unique (org, version) lets only one write version 2."""
    keyring, store = two_org_keyring()
    v1_only = [store.records[(ORG_A, 1)]]

    async def stale_load(org_id: str) -> list[OrgKeyRecord]:
        return v1_only

    racing_store = InMemoryOrgKeyStore()
    racing_store.load = stale_load  # type: ignore[method-assign]  # the racer read before the winner wrote
    racing_store.add = store.add  # type: ignore[method-assign]  # both write to the same store
    run(keyring.rotate(ORG_A))
    with pytest.raises(KeyVersionConflictError):
        run(Keyring(keyring.master, racing_store).rotate(ORG_A))
    assert run(Keyring(keyring.master, store).active(ORG_A)).version == 2


def test_records_must_be_internally_consistent() -> None:
    _, store = two_org_keyring()
    record = store.records[(ORG_A, 1)]
    with pytest.raises(ValueError, match="version"):
        OrgKeyRecord(ORG_A, 0, record.master_key_id, record.wrapped, record.created_at)
    with pytest.raises(ValueError, match="purpose"):
        OrgKeyRecord(
            ORG_A, 1, record.master_key_id, {KeyPurpose.DATA: record.wrapped[KeyPurpose.DATA]}, record.created_at
        )


def test_a_store_returning_another_orgs_record_is_refused() -> None:
    keyring, store = two_org_keyring()

    async def leaky_load(org_id: str) -> list[OrgKeyRecord]:
        return [store.records[(ORG_B, 1)]]

    store.load = leaky_load  # type: ignore[method-assign]  # simulate a store that ignores the org
    with pytest.raises(KeyUnwrapError, match="another org"):
        run(Keyring(keyring.master, store).active(ORG_A))
