"""Keyring errors. Messages name orgs, versions and purposes, never key material or values."""

__all__ = [
    "CryptoError",
    "DecryptionError",
    "KeyUnwrapError",
    "KeyVersionConflictError",
    "MasterKeyError",
    "NoOrgKeysError",
    "UnknownKeyVersionError",
]


class CryptoError(Exception):
    """Base class: every keyring failure fails closed with one of these."""


class MasterKeyError(CryptoError):
    """`PII_MASTER_KEY` is missing or malformed. Raised at startup so the service never runs without it."""


class KeyUnwrapError(CryptoError):
    """An org key record cannot be opened: wrong master key, tampering, or a malformed record."""


class DecryptionError(CryptoError):
    """Ciphertext does not open under this org's key and associated data."""


class NoOrgKeysError(CryptoError):
    """The org has no keys yet: create them when the org is provisioned."""


class UnknownKeyVersionError(CryptoError):
    """Ciphertext names a key version the org does not have."""


class KeyVersionConflictError(CryptoError):
    """That key version already exists for the org (keys created twice, or a concurrent rotation)."""
