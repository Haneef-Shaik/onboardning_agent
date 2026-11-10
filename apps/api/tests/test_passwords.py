"""bcrypt password hashes (PRD §7.8)."""

import pytest
from onb_api.auth.passwords import MAX_PASSWORD_BYTES, hash_password, verify_password


def test_a_hash_verifies_its_password_only() -> None:
    hashed = hash_password("correct horse", rounds=4)
    assert hashed.startswith("$2b$04$")
    assert verify_password("correct horse", hashed)
    assert not verify_password("correct horsE", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("same", rounds=4) != hash_password("same", rounds=4)


def test_a_password_bcrypt_would_truncate_is_refused_when_hashing() -> None:
    with pytest.raises(ValueError, match="72"):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1), rounds=4)


def test_an_empty_password_is_refused_when_hashing() -> None:
    with pytest.raises(ValueError, match="empty"):
        hash_password("", rounds=4)


def test_a_too_long_password_never_verifies() -> None:
    hashed = hash_password("x" * MAX_PASSWORD_BYTES, rounds=4)
    assert not verify_password("x" * MAX_PASSWORD_BYTES + "y", hashed)


def test_no_hash_never_verifies() -> None:
    assert not verify_password("anything", None)


def test_a_malformed_hash_never_verifies() -> None:
    assert not verify_password("anything", "not-a-bcrypt-hash")
