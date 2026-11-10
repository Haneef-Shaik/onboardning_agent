"""Keyring errors. Messages name orgs, versions and purposes, never key material or values."""

__all__ = [
    "CryptoError",
    "DecryptionError",
    "KeyRewrapConflictError",
    "KeyUnwrapError",
    "KeyVersionConflictError",
    "MasterKeyError",
    "NoOrgKeysError",
    "SupersededTokenError",
    "TokenBindingError",
    "TokenNotFoundError",
    "UnknownKeyVersionError",
    "VaultConflictError",
    "VaultError",
    "VaultIntegrityError",
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


class KeyRewrapConflictError(CryptoError):
    """A master-key re-wrap could not complete cleanly (a concurrent change); run it again."""


class VaultError(CryptoError):
    """Base class for vault failures. A read that raises one releases no value (fails closed)."""


class VaultIntegrityError(VaultError):
    """A vault record is malformed, in the wrong partition, or its `sig` does not verify: it was
    changed outside the vault (PRD §7.10 Integrity)."""


class TokenNotFoundError(VaultError):
    """No record with that token in this org and partition (or the token is not one the vault issues)."""


class TokenBindingError(VaultError):
    """The record is intact but belongs to another subject or field than the reader expected."""


class SupersededTokenError(VaultError):
    """The token's value was replaced by a newer version; read it only with `allow_superseded=True`."""


class VaultConflictError(VaultError):
    """Another write to the same subject and field won the race; reload and try again."""
