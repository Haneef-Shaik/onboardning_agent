"""PII vault crypto (PRD §7.10): per-org keyring, the signed encrypted vault, tokens, masks, blind indexes."""

from onb_crypto.blind_index import MIN_INDEX_KEY_BYTES, BlindIndexField, blind_index
from onb_crypto.envelope import WRAP_ALG, WrappedKey, unwrap_key, wrap_key
from onb_crypto.errors import (
    CryptoError,
    DecryptionError,
    KeyRewrapConflictError,
    KeyUnwrapError,
    KeyVersionConflictError,
    MasterKeyError,
    NoOrgKeysError,
    SupersededTokenError,
    TokenBindingError,
    TokenNotFoundError,
    UnknownKeyVersionError,
    VaultConflictError,
    VaultError,
    VaultIntegrityError,
)
from onb_crypto.key_store import (
    ORG_KEYS_COLLECTION,
    OrgKeyRecord,
    OrgKeyStore,
    RepositoryOrgKeyStore,
    ensure_org_keys_index,
)
from onb_crypto.keyring import Keyring, OrgKeys, Sealed, associated_data
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
from onb_crypto.tokens import PTK_PREFIX, TOKEN_LENGTH, is_token, new_token
from onb_crypto.vault import CONSENT_COLLECTION, PURGED, AadhaarConsent, Vault, VaultRepository, VaultStore
from onb_crypto.vault_indexes import ensure_vault_indexes
from onb_crypto.vault_record import (
    AADHAAR_VAULT_COLLECTION,
    VAULT_COLLECTION,
    SubjectType,
    VaultField,
    VaultRecord,
    VaultStatus,
)

__all__ = [
    "AADHAAR_VAULT_COLLECTION",
    "CONSENT_COLLECTION",
    "KEY_BYTES",
    "MASTER_KEY_ENV",
    "MIN_INDEX_KEY_BYTES",
    "ORG_KEYS_COLLECTION",
    "PROJECT",
    "PTK_PREFIX",
    "PURGED",
    "TOKEN_LENGTH",
    "VAULT_COLLECTION",
    "WRAP_ALG",
    "AadhaarConsent",
    "BlindIndexField",
    "CryptoError",
    "DecryptionError",
    "KeyPurpose",
    "KeyRewrapConflictError",
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
    "SubjectType",
    "SupersededTokenError",
    "TokenBindingError",
    "TokenNotFoundError",
    "UnknownKeyVersionError",
    "Vault",
    "VaultConflictError",
    "VaultError",
    "VaultField",
    "VaultIntegrityError",
    "VaultRecord",
    "VaultRepository",
    "VaultStatus",
    "VaultStore",
    "WrappedKey",
    "associated_data",
    "blind_index",
    "ensure_org_keys_index",
    "ensure_vault_indexes",
    "is_token",
    "mask",
    "mask_aadhaar",
    "mask_bank_account",
    "mask_email",
    "mask_mobile",
    "mask_pan",
    "new_token",
    "rewrap_orgs",
    "rotate_orgs",
    "unwrap_key",
    "verify_orgs",
    "wrap_key",
]

PROJECT = "py-crypto"
