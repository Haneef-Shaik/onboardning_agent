"""PII vault crypto (PRD §7.10): per-org keyring, display masks and blind indexes."""

from onb_crypto.blind_index import MIN_INDEX_KEY_BYTES, BlindIndexField, blind_index
from onb_crypto.envelope import WRAP_ALG, WrappedKey, unwrap_key, wrap_key
from onb_crypto.errors import (
    CryptoError,
    DecryptionError,
    KeyUnwrapError,
    KeyVersionConflictError,
    MasterKeyError,
    NoOrgKeysError,
    UnknownKeyVersionError,
)
from onb_crypto.key_store import (
    ORG_KEYS_COLLECTION,
    OrgKeyRecord,
    OrgKeyStore,
    RepositoryOrgKeyStore,
    ensure_org_keys_index,
)
from onb_crypto.keyring import Keyring, OrgKeys, Sealed
from onb_crypto.keys import KEY_BYTES, MASTER_KEY_ENV, KeyPurpose, MasterKey, SecretKey
from onb_crypto.masks import (
    MaskedField,
    mask,
    mask_aadhaar,
    mask_bank_account,
    mask_email,
    mask_mobile,
    mask_pan,
)
from onb_crypto.rotation import rewrap_orgs, rotate_orgs, verify_orgs

__all__ = [
    "KEY_BYTES",
    "MASTER_KEY_ENV",
    "MIN_INDEX_KEY_BYTES",
    "ORG_KEYS_COLLECTION",
    "PROJECT",
    "WRAP_ALG",
    "BlindIndexField",
    "CryptoError",
    "DecryptionError",
    "KeyPurpose",
    "KeyUnwrapError",
    "KeyVersionConflictError",
    "Keyring",
    "MaskedField",
    "MasterKey",
    "MasterKeyError",
    "NoOrgKeysError",
    "OrgKeyRecord",
    "OrgKeyStore",
    "OrgKeys",
    "RepositoryOrgKeyStore",
    "Sealed",
    "SecretKey",
    "UnknownKeyVersionError",
    "WrappedKey",
    "blind_index",
    "ensure_org_keys_index",
    "mask",
    "mask_aadhaar",
    "mask_bank_account",
    "mask_email",
    "mask_mobile",
    "mask_pan",
    "rewrap_orgs",
    "rotate_orgs",
    "unwrap_key",
    "verify_orgs",
    "wrap_key",
]

PROJECT = "py-crypto"
