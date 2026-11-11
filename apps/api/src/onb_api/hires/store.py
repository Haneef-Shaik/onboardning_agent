"""`new_hires` through the tenant repository (PRD §9: PII as tokens + blind indexes).

A hire document:

    {_id: "h_…", org_id, name, role, location, start_date: "YYYY-MM-DD", manager_id, notes,
     pii: {<field>: "ptk_…"}, pii_masked: {<field>: "<mask>"},
     email_hash, mobile_hash?, pan_hash?, aadhaar_hash?,
     id_proof: {aadhaar_consent: bool, alternative_id}, created_by, created_at}

`{org_id, email_hash}` is unique among hires that have an `email_hash` (a partial index: hires seeded
without one do not collide), so the same email cannot be used twice in one org, while the per-org
index key makes one email index differently in each org.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from onb_data import DataStore, TenantRepository
from pymongo.errors import DuplicateKeyError

__all__ = ["HIRES_COLLECTION", "EmailTakenError", "HireStore", "StoredPii"]

log = logging.getLogger(__name__)

HIRES_COLLECTION: Final = "new_hires"
EMAIL_INDEX: Final = "org_email_hash_unique"


@dataclass(frozen=True, slots=True)
class StoredPii:
    """What a hire keeps of one vault value: its token, vault version, mask and blind index."""

    field: str
    token: str
    version: int
    masked: str
    index: tuple[str, str] | None  # (index name, blind index)


class EmailTakenError(Exception):
    """Another hire of this org has this email (by blind index)."""


class HireStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def _repo(self) -> TenantRepository:
        return self._store.tenant(HIRES_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(
            HIRES_COLLECTION,
            [("org_id", 1), ("email_hash", 1)],
            unique=True,
            name=EMAIL_INDEX,
            partial={"email_hash": {"$exists": True}},
        )
        await self._store.ensure_index(HIRES_COLLECTION, [("org_id", 1), ("manager_id", 1)])

    async def email_taken(self, email_hash: str, *, other_than: str | None = None) -> bool:
        query: dict[str, Any] = {"email_hash": email_hash}
        if other_than is not None:
            query["_id"] = {"$ne": other_than}
        count: int = await self._repo().count(query)
        return count > 0

    async def insert(self, document: Mapping[str, Any]) -> None:
        try:
            await self._repo().insert_one(document)
        except DuplicateKeyError as error:
            raise _taken_or(error) from None

    async def get(self, hire_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = await self._repo().find_one({"_id": hire_id})
        return found

    async def delete(self, hire_id: str) -> None:
        await self._repo().delete_one({"_id": hire_id})

    async def page(self, query: Mapping[str, Any], *, page: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        repo = self._repo()
        total: int = await repo.count(query)
        found: list[dict[str, Any]] = await repo.find(
            query, sort=[("created_at", -1), ("_id", 1)], skip=(page - 1) * limit, limit=limit
        )
        return found, total

    async def set_fields(self, hire_id: str, values: Mapping[str, Any]) -> bool:
        """`$set` on one hire; False when it does not exist. A taken email raises `EmailTakenError`."""
        try:
            outcome = await self._repo().update_one({"_id": hire_id}, {"$set": dict(values)})
        except DuplicateKeyError as error:
            raise _taken_or(error) from None
        return bool(outcome.matched == 1)

    async def commit_pii(self, hire_id: str, pii: StoredPii, extra: Mapping[str, Any]) -> bool:
        """Point the hire's field at a new vault value, only if that value is newer than the one it holds
        (a hire seeded without versions takes any). False when the hire is gone or holds a newer one."""
        version_path = f"pii_versions.{pii.field}"
        changes: dict[str, Any] = {
            f"pii.{pii.field}": pii.token,
            f"pii_masked.{pii.field}": pii.masked,
            version_path: pii.version,
            **extra,
        }
        if pii.index is not None:
            changes[pii.index[0]] = pii.index[1]
        newer = {"$or": [{version_path: {"$exists": False}}, {version_path: {"$lt": pii.version}}]}
        try:
            outcome = await self._repo().update_one({"_id": hire_id, **newer}, {"$set": changes})
        except DuplicateKeyError as error:
            raise _taken_or(error) from None
        return bool(outcome.matched == 1)

    async def restore_email(self, hire_id: str, email_hash: str | None) -> None:
        """Put back the hire's previous email index after an update that did not complete (best effort)."""
        update = {"$unset": {"email_hash": ""}} if email_hash is None else {"$set": {"email_hash": email_hash}}
        try:
            await self._repo().update_one({"_id": hire_id}, update)
        except Exception as error:
            log.error("email index of hire %s not restored: %s", hire_id, type(error).__name__)


def _taken_or(error: DuplicateKeyError) -> Exception:
    """The email index's refusal is `EmailTakenError`; any other duplicate (an `_id`) stays as it was."""
    return EmailTakenError("email already used by a hire of this org") if EMAIL_INDEX in str(error) else error
