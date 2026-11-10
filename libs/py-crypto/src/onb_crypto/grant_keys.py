"""The orchestrator's grant keys (PRD §7.10 Signed grants): Ed25519, so sinks verify with a public key
and can never sign a grant themselves.

- `PII_GRANT_SIGNING_KEY`: the orchestrator's 32-byte Ed25519 private key, base64. Only the
  orchestrator process has it.
- `PII_GRANT_VERIFY_KEYS`: one or more base64 32-byte Ed25519 public keys, comma-separated (more than
  one during a rotation). Every sink has them.

Make a key pair with `python -m onb_crypto.grant_keys`. Neither key ever appears in a repr, log or
error message.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from typing import Final, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from onb_crypto.errors import CryptoError

__all__ = ["GRANT_SIGNING_KEY_ENV", "GRANT_VERIFY_KEYS_ENV", "GrantKeyError", "GrantSigningKey", "GrantVerifyKey"]

GRANT_SIGNING_KEY_ENV: Final = "PII_GRANT_SIGNING_KEY"
GRANT_VERIFY_KEYS_ENV: Final = "PII_GRANT_VERIFY_KEYS"
_KEY_BYTES: Final = 32
_KID_LABEL: Final = b"onb.pii-grant-kid.v1"
_KID_HEX_CHARS: Final = 16


class GrantKeyError(CryptoError):
    """A grant key is missing or malformed. Raised at startup so no process runs without it."""


def _decode_key(encoded: object, what: str) -> bytes:
    if not isinstance(encoded, str) or not encoded.strip():
        raise GrantKeyError(f"{what} is missing")
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise GrantKeyError(f"{what} is not valid base64") from None
    if len(raw) != _KEY_BYTES:
        raise GrantKeyError(f"{what} must decode to exactly {_KEY_BYTES} bytes")
    return raw


class GrantVerifyKey:
    """An Ed25519 public key that verifies grants. `kid` names it in the JWS header."""

    __slots__ = ("_key", "_raw", "kid")

    def __init__(self, raw: bytes) -> None:
        if not isinstance(raw, bytes) or len(raw) != _KEY_BYTES:
            raise GrantKeyError(f"a grant verify key is exactly {_KEY_BYTES} bytes")
        self._raw = raw
        self._key = Ed25519PublicKey.from_public_bytes(raw)
        self.kid = hashlib.sha256(_KID_LABEL + raw).hexdigest()[:_KID_HEX_CHARS]

    @classmethod
    def from_encoded(cls, encoded: str) -> GrantVerifyKey:
        return cls(_decode_key(encoded, GRANT_VERIFY_KEYS_ENV))

    def encoded(self) -> str:
        return base64.b64encode(self._raw).decode("ascii")

    def raw(self) -> bytes:
        return self._raw

    def verifies(self, signature: bytes, message: bytes) -> bool:
        try:
            self._key.verify(signature, message)
        except InvalidSignature:
            return False
        return True

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GrantVerifyKey):
            return NotImplemented
        return hmac.compare_digest(self._raw, other._raw)

    def __hash__(self) -> int:
        return hash(self._raw)

    def __repr__(self) -> str:
        return f"GrantVerifyKey(kid={self.kid})"


class GrantSigningKey:
    """The orchestrator's Ed25519 private key. `repr`, `str` and pickling never expose it."""

    __slots__ = ("_key", "_seed", "public_key")

    def __init__(self, seed: bytes) -> None:
        if not isinstance(seed, bytes) or len(seed) != _KEY_BYTES:
            raise GrantKeyError(f"a grant signing key is exactly {_KEY_BYTES} bytes")
        self._seed = seed
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.public_key = GrantVerifyKey(self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    @classmethod
    def generate(cls) -> GrantSigningKey:
        seed = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return cls(seed)

    @classmethod
    def from_encoded(cls, encoded: str) -> GrantSigningKey:
        return cls(_decode_key(encoded, GRANT_SIGNING_KEY_ENV))

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GrantSigningKey:
        return cls.from_encoded(env.get(GRANT_SIGNING_KEY_ENV, ""))

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)

    def encoded(self) -> str:
        """For writing the key into a secret store once; never log it."""
        return base64.b64encode(self._seed).decode("ascii")

    def reveal(self) -> bytes:
        return self._seed

    def __repr__(self) -> str:
        return f"GrantSigningKey(<redacted>, kid={self.public_key.kid})"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a grant signing key cannot be pickled")


def verify_keys_from_env(env: Mapping[str, str]) -> list[GrantVerifyKey]:
    parts = [part.strip() for part in env.get(GRANT_VERIFY_KEYS_ENV, "").split(",")]
    keys = [GrantVerifyKey.from_encoded(part) for part in parts if part]
    if not keys:
        raise GrantKeyError(f"{GRANT_VERIFY_KEYS_ENV} is missing")
    return keys


if __name__ == "__main__":  # pragma: no cover - operator helper
    pair = GrantSigningKey.generate()
    print(f"{GRANT_SIGNING_KEY_ENV}={pair.encoded()}")
    print(f"{GRANT_VERIFY_KEYS_ENV}={pair.public_key.encoded()}")
