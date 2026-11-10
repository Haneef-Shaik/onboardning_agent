"""Key objects that never show their material, and the master key from `PII_MASTER_KEY` (PRD §7.10).

The master key is 32 random bytes, base64-encoded in the environment (`openssl rand -base64 32`).
It only wraps and unwraps per-org keys; it never encrypts data itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from collections.abc import Mapping
from enum import StrEnum
from typing import Final, NoReturn

from onb_crypto.errors import MasterKeyError

__all__ = ["KEY_BYTES", "MASTER_KEY_ENV", "KeyPurpose", "MasterKey", "SecretKey"]

KEY_BYTES: Final = 32
MASTER_KEY_ENV: Final = "PII_MASTER_KEY"
_KEY_ID_LABEL: Final = b"onb.master-key-id.v1"
_KEY_ID_HEX_CHARS: Final = 16


class KeyPurpose(StrEnum):
    """One org key per purpose, so no key is used for two jobs (PRD §7.10)."""

    DATA = "data"  # AES-256-GCM for vault values
    SIGNING = "signing"  # HMAC-SHA256 `sig` over vault records
    INDEX = "index"  # HMAC blind indexes: email_hash, mobile_hash, pan_hash
    AADHAAR = "aadhaar"  # AES-256-GCM for the pii_vault_aadhaar partition
    AADHAAR_INDEX = "aadhaar_index"  # HMAC blind index aadhaar_hash

    @property
    def encrypts(self) -> bool:
        return self in _ENCRYPTION_PURPOSES

    @property
    def rotates(self) -> bool:
        """Index keys are carried forward on rotation: stored blind indexes must keep matching."""
        return self not in _INDEX_PURPOSES


_ENCRYPTION_PURPOSES: Final = frozenset({KeyPurpose.DATA, KeyPurpose.AADHAAR})
_INDEX_PURPOSES: Final = frozenset({KeyPurpose.INDEX, KeyPurpose.AADHAAR_INDEX})


class SecretKey:
    """32 bytes of key material. `repr`, `str` and pickling never expose it; `reveal()` is explicit."""

    __slots__ = ("_material",)

    def __init__(self, material: bytes) -> None:
        if not isinstance(material, bytes):
            raise TypeError("key material must be bytes")
        if len(material) != KEY_BYTES:
            raise ValueError(f"key material must be exactly {KEY_BYTES} bytes")
        self._material = material

    @classmethod
    def generate(cls) -> SecretKey:
        return cls(secrets.token_bytes(KEY_BYTES))

    def reveal(self) -> bytes:
        return self._material

    def __repr__(self) -> str:
        return "SecretKey(<redacted>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretKey):
            return NotImplemented
        return hmac.compare_digest(self._material, other._material)

    __hash__ = None  # type: ignore[assignment]  # keys are compared in constant time, never hashed

    def __reduce__(self) -> NoReturn:
        raise TypeError("key material cannot be pickled")


class MasterKey:
    """`PII_MASTER_KEY`. `key_id` is a public fingerprint so operators can tell which master wrapped a record."""

    __slots__ = ("_key", "_key_id")

    def __init__(self, key: SecretKey) -> None:
        self._key = key
        digest = hmac.new(key.reveal(), _KEY_ID_LABEL, hashlib.sha256).hexdigest()
        self._key_id = digest[:_KEY_ID_HEX_CHARS]

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MasterKey:
        """Fail fast at startup: missing, short or malformed raises `MasterKeyError`."""
        value = (os.environ if environ is None else environ).get(MASTER_KEY_ENV, "").strip()
        if not value:
            raise MasterKeyError(f"{MASTER_KEY_ENV} is not set: generate one with `openssl rand -base64 32`")
        return cls.from_encoded(value)

    @classmethod
    def from_encoded(cls, value: str) -> MasterKey:
        try:
            material = base64.b64decode(value.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise MasterKeyError(f"{MASTER_KEY_ENV} must be base64 of exactly {KEY_BYTES} bytes") from None
        if len(material) != KEY_BYTES:
            raise MasterKeyError(
                f"{MASTER_KEY_ENV} must be base64 of exactly {KEY_BYTES} bytes (it decodes to {len(material)})"
            )
        return cls(SecretKey(material))

    @property
    def key(self) -> SecretKey:
        return self._key

    @property
    def key_id(self) -> str:
        return self._key_id

    def __repr__(self) -> str:
        return f"MasterKey(key_id={self._key_id}, key=<redacted>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MasterKey):
            return NotImplemented
        return self._key == other._key

    __hash__ = None  # type: ignore[assignment]  # holds a key; compared in constant time, never hashed

    def __reduce__(self) -> NoReturn:
        raise TypeError("key material cannot be pickled")
