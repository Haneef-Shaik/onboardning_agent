"""Envelope encryption: each org key is wrapped with AES-256-GCM under the master key (PRD §7.10).

The associated data binds a wrapped key to its org, version and purpose, so a wrapped key copied to
another org, version or purpose does not unwrap.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from onb_crypto.errors import KeyUnwrapError
from onb_crypto.keys import KeyPurpose, MasterKey, SecretKey

__all__ = ["NONCE_BYTES", "WRAP_ALG", "WrappedKey", "unwrap_key", "wrap_key"]

WRAP_ALG: Final = "A256GCM"
NONCE_BYTES: Final = 12
_SEP: Final = b"\x1f"
_WRAP_LABEL: Final = b"onb.org-key.v1"


@dataclass(frozen=True, slots=True)
class WrappedKey:
    nonce: bytes
    ciphertext: bytes


def _wrap_binding(org_id: str, version: int, purpose: KeyPurpose) -> bytes:
    return _SEP.join((_WRAP_LABEL, org_id.encode(), str(version).encode(), purpose.value.encode()))


def wrap_key(master: MasterKey, key: SecretKey, *, org_id: str, version: int, purpose: KeyPurpose) -> WrappedKey:
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(master.key.reveal()).encrypt(nonce, key.reveal(), _wrap_binding(org_id, version, purpose))
    return WrappedKey(nonce, ciphertext)


def unwrap_key(master: MasterKey, wrapped: WrappedKey, *, org_id: str, version: int, purpose: KeyPurpose) -> SecretKey:
    """The org key, in memory only. Any failure raises `KeyUnwrapError` and returns nothing."""
    if len(wrapped.nonce) != NONCE_BYTES:
        raise KeyUnwrapError(f"org key {org_id} v{version} {purpose.value} has a malformed nonce")
    try:
        material = AESGCM(master.key.reveal()).decrypt(
            wrapped.nonce, wrapped.ciphertext, _wrap_binding(org_id, version, purpose)
        )
        return SecretKey(material)
    except (InvalidTag, ValueError, TypeError):
        raise KeyUnwrapError(
            f"org key {org_id} v{version} {purpose.value} cannot be unwrapped: wrong master key or tampered record"
        ) from None
