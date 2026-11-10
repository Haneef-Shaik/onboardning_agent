"""Test doubles for the keyring's storage. Test keys here are random per run, never fixed secrets."""

from __future__ import annotations

import asyncio
import base64
import secrets
from collections.abc import Coroutine, Mapping
from typing import Any

from onb_crypto import KeyVersionConflictError, MasterKey, OrgKeyRecord
from onb_data import tenant_scope

ORG_A = "org_alpha"
ORG_B = "org_beta"


def encoded_master_key() -> str:
    """A fresh base64 master key, the format `PII_MASTER_KEY` takes."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def new_master_key() -> MasterKey:
    return MasterKey.from_encoded(encoded_master_key())


def in_org[T](org_id: str, coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` as `org_id`, the way a request authenticated for that org would (PRD §7.9)."""
    with tenant_scope(org_id):
        return asyncio.run(coro)


class InMemoryOrgKeyStore:
    """An `OrgKeyStore` in a dict, keyed by (org_id, version). Records are immutable, so sharing is safe."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, int], OrgKeyRecord] = {}

    async def load(self, org_id: str) -> list[OrgKeyRecord]:
        return sorted((r for (org, _), r in self.records.items() if org == org_id), key=lambda r: r.version)

    async def add(self, record: OrgKeyRecord) -> None:
        key = (record.org_id, record.version)
        if key in self.records:
            raise KeyVersionConflictError("version exists")
        self.records = {**self.records, key: record}

    async def replace(self, record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
        key = (record.org_id, record.version)
        current = self.records.get(key)
        if current is None or current.master_key_id != expected_master_key_id:
            return False
        self.records = {**self.records, key: record}
        return True


class FakeTenantRepository:
    """Records the calls `RepositoryOrgKeyStore` makes; mimics `TenantRepository` for one org."""

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self.documents: list[dict[str, Any]] = []
        self.duplicate_on_insert = False
        self.matched = 1

    async def find(self, query: Mapping[str, Any] | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        self.last_find = (dict(query or {}), kwargs)
        return [dict(d) for d in self.documents]

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        if self.duplicate_on_insert:
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("E11000 duplicate key")
        stored = {**document, "org_id": self.org_id, "_id": len(self.documents) + 1}
        self.documents = [*self.documents, stored]
        return stored["_id"]

    async def update_one(self, query: Mapping[str, Any] | None, update: Mapping[str, Any], **kwargs: Any) -> Any:
        self.last_update = (dict(query or {}), dict(update))

        class _Outcome:
            matched = self.matched

        return _Outcome()
