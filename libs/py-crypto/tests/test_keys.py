"""The master key fails fast, and key objects never show their material (PRD §7.10 Encryption)."""

import base64
import pickle
import secrets

import pytest
from onb_crypto import KEY_BYTES, MASTER_KEY_ENV, MasterKey, MasterKeyError, SecretKey

from .fakes import encoded_master_key


def test_master_key_env_name_is_the_prd_name() -> None:
    assert MASTER_KEY_ENV == "PII_MASTER_KEY"
    assert KEY_BYTES == 32


def test_master_key_loads_from_the_environment() -> None:
    encoded = encoded_master_key()
    master = MasterKey.from_env({MASTER_KEY_ENV: encoded})
    assert master.key.reveal() == base64.b64decode(encoded)


def test_surrounding_whitespace_is_ignored() -> None:
    encoded = encoded_master_key()
    assert MasterKey.from_env({MASTER_KEY_ENV: f"  {encoded}\n"}) == MasterKey.from_encoded(encoded)


@pytest.mark.parametrize("environ", [{}, {MASTER_KEY_ENV: ""}, {MASTER_KEY_ENV: "   "}])
def test_a_missing_master_key_fails_fast(environ: dict[str, str]) -> None:
    with pytest.raises(MasterKeyError, match="PII_MASTER_KEY is not set"):
        MasterKey.from_env(environ)


@pytest.mark.parametrize("length", [0, 1, 16, 31])
def test_a_short_master_key_fails_fast(length: int) -> None:
    short = base64.b64encode(secrets.token_bytes(length)).decode() or "AA=="
    with pytest.raises(MasterKeyError, match="32 bytes") as caught:
        MasterKey.from_env({MASTER_KEY_ENV: short})
    assert short not in str(caught.value)


def test_a_long_master_key_is_refused_too() -> None:
    with pytest.raises(MasterKeyError, match="32 bytes"):
        MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(33)).decode())


@pytest.mark.parametrize("bad", ["not base64 at all!", "QUJD$", "a" * 43])
def test_a_master_key_that_is_not_base64_fails_without_echoing_it(bad: str) -> None:
    with pytest.raises(MasterKeyError) as caught:
        MasterKey.from_encoded(bad)
    assert bad not in str(caught.value)


def test_the_master_key_id_is_a_stable_fingerprint_not_the_key() -> None:
    encoded = encoded_master_key()
    one, two = MasterKey.from_encoded(encoded), MasterKey.from_encoded(encoded)
    other = MasterKey.from_encoded(encoded_master_key())
    assert one.key_id == two.key_id
    assert one.key_id != other.key_id
    assert len(one.key_id) == 16
    assert one.key_id not in one.key.reveal().hex()


def test_secret_keys_have_exactly_32_bytes() -> None:
    assert len(SecretKey.generate().reveal()) == KEY_BYTES
    assert SecretKey.generate() != SecretKey.generate()
    with pytest.raises(ValueError, match="32 bytes"):
        SecretKey(b"short")
    with pytest.raises(TypeError):
        SecretKey("a" * 32)  # type: ignore[arg-type]  # the check under test refuses str


def test_keys_never_show_their_material() -> None:
    master = MasterKey.from_encoded(encoded_master_key())
    key = SecretKey.generate()
    for obj, material in ((key, key.reveal()), (master, master.key.reveal())):
        for text in (repr(obj), str(obj), f"{obj}", f"{obj!r}"):
            assert material.hex() not in text
            assert base64.b64encode(material).decode() not in text
            assert "redacted" in text


def test_keys_cannot_be_pickled() -> None:
    with pytest.raises(TypeError, match="pickled"):
        pickle.dumps(SecretKey.generate())
    with pytest.raises(TypeError, match="pickled"):
        pickle.dumps(MasterKey.from_encoded(encoded_master_key()))


def test_keys_compare_by_value_and_are_not_hashable() -> None:
    key = SecretKey.generate()
    assert key == SecretKey(key.reveal())
    assert key != "not a key"
    with pytest.raises(TypeError):
        hash(key)
