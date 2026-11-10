"""Where wrapped org keys live: the tenant collection `org_keys`, one document per (org, key version).

Documents hold wrapped keys only. Reads are validated field by field and fail closed: the database is
not trusted (PRD §7.10 Integrity). All access goes through a T-1 `TenantRepository` (PRD §7.9), and
`ensure_org_keys_index` makes `{org_id, key_version}` unique, which is what serialises rotations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Protocol

from onb_data import DataStore, TenantRepository
from pymongo.errors import DuplicateKeyError

from onb_crypto.envelope import WRAP_ALG, WrappedKey
from onb_crypto.errors import KeyUnwrapError, KeyVersionConflictError
from onb_crypto.keys import KeyPurpose

__all__ = [
    "ORG_KEYS_COLLECTION",
    "OrgKeyRecord",
    "OrgKeyStore",
    "RepositoryOrgKeyStore",
    "ensure_org_keys_index",
]

ORG_KEYS_COLLECTION: Final = "org_keys"
_DOCUMENT_FIELDS: Final = frozenset({"_id", "org_id", "key_version", "master_key_id", "created_at", "keys", "wrap_alg"})


@dataclass(frozen=True, slots=True)
class OrgKeyRecord:
    """One key version of one org: a wrapped key for every purpose."""

    org_id: str
    version: int
    master_key_id: str
    wrapped: Mapping[KeyPurpose, WrappedKey]
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("key version must be a positive integer")
        if set(self.wrapped) != set(KeyPurpose):
            raise ValueError("an org key record needs exactly one wrapped key per purpose")
        object.__setattr__(self, "wrapped", MappingProxyType(dict(self.wrapped)))


class OrgKeyStore(Protocol):
    async def load(self, org_id: str) -> list[OrgKeyRecord]:
        """Every key version of the org, oldest first."""
        ...

    async def add(self, record: OrgKeyRecord) -> None:
        """Insert a new version; `KeyVersionConflictError` if that version exists."""
        ...

    async def replace(self, record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
        """Re-wrapped keys for an existing version, only if it is still wrapped by the expected master."""
        ...


async def ensure_org_keys_index(store: DataStore) -> None:
    await store.ensure_index(ORG_KEYS_COLLECTION, [("org_id", 1), ("key_version", 1)], unique=True)


def _wrapped_to_document(wrapped: Mapping[KeyPurpose, WrappedKey]) -> dict[str, dict[str, bytes]]:
    return {p.value: {"nonce": w.nonce, "ciphertext": w.ciphertext} for p, w in wrapped.items()}


def _malformed(org_id: str, what: str) -> KeyUnwrapError:
    return KeyUnwrapError(f"org key record for {org_id} is malformed: {what}")


def _wrapped_from_document(org_id: str, keys: object) -> dict[KeyPurpose, WrappedKey]:
    if not isinstance(keys, Mapping) or set(keys) != {p.value for p in KeyPurpose}:
        raise _malformed(org_id, "keys must hold exactly one entry per purpose")
    wrapped: dict[KeyPurpose, WrappedKey] = {}
    for purpose in KeyPurpose:
        entry = keys[purpose.value]
        if not isinstance(entry, Mapping) or set(entry) != {"nonce", "ciphertext"}:
            raise _malformed(org_id, f"{purpose.value} entry")
        nonce, ciphertext = entry["nonce"], entry["ciphertext"]
        if not isinstance(nonce, bytes) or not isinstance(ciphertext, bytes):
            raise _malformed(org_id, f"{purpose.value} entry is not binary")
        wrapped[purpose] = WrappedKey(nonce, ciphertext)
    return wrapped


def _record_from_document(org_id: str, doc: Mapping[str, Any]) -> OrgKeyRecord:
    if doc.get("org_id") != org_id:
        raise KeyUnwrapError(f"org key store returned a record of another org for {org_id}")
    if not set(doc) <= _DOCUMENT_FIELDS or doc.get("wrap_alg") != WRAP_ALG:
        raise _malformed(org_id, "unexpected fields or wrap algorithm")
    version, master_key_id, created_at = doc.get("key_version"), doc.get("master_key_id"), doc.get("created_at")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _malformed(org_id, "key_version")
    if not isinstance(master_key_id, str) or not isinstance(created_at, datetime):
        raise _malformed(org_id, "master_key_id or created_at")
    return OrgKeyRecord(org_id, version, master_key_id, _wrapped_from_document(org_id, doc.get("keys")), created_at)


class RepositoryOrgKeyStore:
    """`OrgKeyStore` over `store.tenant("org_keys")`. Calls need `tenant_scope(org_id)`; the repository
    refuses an `org_id` that differs from the tenant context."""

    __slots__ = ("_repo",)

    def __init__(self, repository: TenantRepository) -> None:
        self._repo = repository

    async def load(self, org_id: str) -> list[OrgKeyRecord]:
        docs = await self._repo.find({"org_id": org_id}, sort=[("key_version", 1)])
        return sorted((_record_from_document(org_id, d) for d in docs), key=lambda r: r.version)

    async def add(self, record: OrgKeyRecord) -> None:
        document = {
            "org_id": record.org_id,
            "key_version": record.version,
            "master_key_id": record.master_key_id,
            "wrap_alg": WRAP_ALG,
            "created_at": record.created_at,
            "keys": _wrapped_to_document(record.wrapped),
        }
        try:
            await self._repo.insert_one(document)
        except DuplicateKeyError:
            raise KeyVersionConflictError(f"org {record.org_id} already has key version {record.version}") from None

    async def replace(self, record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
        outcome = await self._repo.update_one(
            {"org_id": record.org_id, "key_version": record.version, "master_key_id": expected_master_key_id},
            {"$set": {"master_key_id": record.master_key_id, "keys": _wrapped_to_document(record.wrapped)}},
        )
        return bool(outcome.matched == 1)
