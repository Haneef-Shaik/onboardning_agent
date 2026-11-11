"""`pii_access_log` (PRD FR-P5): every reveal and every token resolution, for HR_ADMIN.

Two writers share the collection: this module records reveals (`kind: "reveal"`, a user, a reason);
PII-4's sink records resolutions (`kind: "resolve"`, a service, its run and task). Both carry the hire
(`subject.id`), the field, the token and a masked copy of the value, never the value itself. The API
writes only through `record_reveal` (inserts) and reads through `page`; there is no update or delete.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from onb_contracts.hires import (
    PiiAccessActor,
    PiiAccessActorType,
    PiiAccessKind,
    PiiAccessRecord,
    PiiField,
)
from onb_crypto import SubjectType
from onb_crypto.sink import ACCESS_LOG_COLLECTION
from onb_data import DataStore, TenantRepository

__all__ = ["AccessQuery", "PiiAccessLog"]


@dataclass(frozen=True, slots=True)
class AccessQuery:
    hire_id: str | None = None
    field: PiiField | None = None
    kind: PiiAccessKind | None = None

    def filter(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        if self.hire_id is not None:
            found["subject.id"] = self.hire_id
        if self.field is not None:
            found["field"] = self.field.value
        if self.kind is not None:
            found["kind"] = self.kind.value
        return found


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _millis(moment: datetime) -> datetime:
    """MongoDB keeps milliseconds: store what reads back, so ordering and tests agree."""
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def _entry_id() -> str:
    """32 hex digits that sort by creation time (nanoseconds first), so entries of one millisecond
    still list newest first."""
    return f"{time.time_ns():016x}{secrets.token_hex(8)}"


def _optional(doc: Mapping[str, Any], key: str) -> str | None:
    value = doc.get(key)
    return None if value is None else str(value)


def record_from_document(doc: Mapping[str, Any]) -> PiiAccessRecord:
    """Raises on a malformed entry: an access log that cannot be read is not shown partially."""
    actor = doc["actor"]
    at: datetime = doc["at"]
    return PiiAccessRecord(
        id=str(doc["_id"]),
        kind=PiiAccessKind(doc["kind"]),
        actor=PiiAccessActor(type=PiiAccessActorType(actor["type"]), id=str(actor["id"])),
        hire_id=str(doc["subject"]["id"]),
        field=PiiField(doc["field"]),
        token=str(doc["token"]),
        masked=str(doc["masked"]),
        reason=_optional(doc, "reason"),
        sink=_optional(doc, "sink"),
        run_id=_optional(doc, "run_id"),
        task_id=_optional(doc, "task_id"),
        at=at if at.tzinfo is not None else at.replace(tzinfo=UTC),
    )


class PiiAccessLog:
    def __init__(self, store: DataStore, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._store = store
        self._clock = clock

    def _repo(self) -> TenantRepository:
        return self._store.tenant(ACCESS_LOG_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(ACCESS_LOG_COLLECTION, [("org_id", 1), ("at", -1), ("_id", -1)])
        await self._store.ensure_index(ACCESS_LOG_COLLECTION, [("org_id", 1), ("subject.id", 1), ("at", -1)])

    async def record_reveal(
        self, *, user_id: str, hire_id: str, field: PiiField, token: str, masked: str, reason: str
    ) -> str:
        """One reveal entry, in the current tenant scope; returns its id. Raises on any store error:
        the caller must not release the value then."""
        entry_id = _entry_id()
        await self._repo().insert_one(
            {
                "_id": entry_id,
                "kind": PiiAccessKind.REVEAL.value,
                "actor": {"type": PiiAccessActorType.USER.value, "id": user_id},
                "subject": {"type": SubjectType.NEW_HIRE.value, "id": hire_id},
                "field": field.value,
                "token": token,
                "masked": masked,
                "reason": reason,
                "at": _millis(self._clock()),
            }
        )
        return entry_id

    async def page(self, query: AccessQuery, *, page: int, limit: int) -> tuple[list[PiiAccessRecord], int]:
        repo = self._repo()
        found = query.filter()
        total = await repo.count(found)
        docs = await repo.find(found, sort=[("at", -1), ("_id", -1)], skip=(page - 1) * limit, limit=limit)
        return [record_from_document(doc) for doc in docs], total
