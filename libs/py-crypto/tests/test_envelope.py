"""Key wrapping: AES-256-GCM under the master key, bound to org, version and purpose (PRD §7.10)."""

import pytest
from onb_crypto import KeyPurpose, KeyUnwrapError, SecretKey, WrappedKey, unwrap_key, wrap_key

from .fakes import ORG_A, ORG_B, new_master_key


def test_a_wrapped_key_unwraps_to_the_same_key() -> None:
    master, key = new_master_key(), SecretKey.generate()
    wrapped = wrap_key(master, key, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    assert unwrap_key(master, wrapped, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA) == key


def test_wrapping_hides_the_key_and_uses_a_fresh_nonce() -> None:
    master, key = new_master_key(), SecretKey.generate()
    one = wrap_key(master, key, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    two = wrap_key(master, key, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    assert key.reveal() not in one.ciphertext
    assert len(one.nonce) == 12
    assert one.nonce != two.nonce
    assert one.ciphertext != two.ciphertext


def test_a_wrong_master_key_fails_closed() -> None:
    key = SecretKey.generate()
    wrapped = wrap_key(new_master_key(), key, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    with pytest.raises(KeyUnwrapError):
        unwrap_key(new_master_key(), wrapped, org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)


@pytest.mark.parametrize(
    ("org_id", "version", "purpose"),
    [
        (ORG_B, 1, KeyPurpose.DATA),
        (ORG_A, 2, KeyPurpose.DATA),
        (ORG_A, 1, KeyPurpose.SIGNING),
        (ORG_A, 1, KeyPurpose.AADHAAR),
    ],
)
def test_a_wrapped_key_moved_to_another_org_version_or_purpose_fails(
    org_id: str, version: int, purpose: KeyPurpose
) -> None:
    master = new_master_key()
    wrapped = wrap_key(master, SecretKey.generate(), org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    with pytest.raises(KeyUnwrapError):
        unwrap_key(master, wrapped, org_id=org_id, version=version, purpose=purpose)


def test_a_tampered_wrapped_key_fails() -> None:
    master = new_master_key()
    wrapped = wrap_key(master, SecretKey.generate(), org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    flipped = bytes([wrapped.ciphertext[0] ^ 1]) + wrapped.ciphertext[1:]
    with pytest.raises(KeyUnwrapError):
        unwrap_key(master, WrappedKey(wrapped.nonce, flipped), org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)


@pytest.mark.parametrize("nonce", [b"", b"x" * 11, b"x" * 13])
def test_a_malformed_wrapped_key_fails_closed(nonce: bytes) -> None:
    master = new_master_key()
    wrapped = wrap_key(master, SecretKey.generate(), org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)
    with pytest.raises(KeyUnwrapError):
        unwrap_key(master, WrappedKey(nonce, wrapped.ciphertext), org_id=ORG_A, version=1, purpose=KeyPurpose.DATA)


def test_the_purposes_are_the_prd_keys() -> None:
    assert {p.value for p in KeyPurpose} == {"data", "signing", "index", "aadhaar", "aadhaar_index"}
