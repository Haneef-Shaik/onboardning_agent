"""Append-only tenant collections: insert, find and count, and nothing else.

- `audit_log` (PRD §7.6): who did what, never edited.
- `template_versions` (PRD FR-T4, TPL-2): a published template version never changes. Its lifecycle
  state (archived or not) lives on the `templates` record, so the version itself needs no update.

`store.append_only("audit_log")` is the only handle on these collections: `store.tenant(...)`,
`shared(...)` and `queue(...)` refuse them, so no caller can reach an update or delete. Reads and the
insert are tenant-scoped with the same rules as `TenantRepository` (`onb_data.scoping`), but this class
is not one: it drives only `insert_one`, `find` and `count_documents` on its collection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from . import scoping
from .context import current_tenant
from .session import session_kwargs

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

__all__ = ["APPEND_ONLY_COLLECTIONS", "AppendOnlyRepository"]

Document = dict[str, Any]

APPEND_ONLY_COLLECTIONS = frozenset({"audit_log", "template_versions"})


class AppendOnlyRepository:
    """A tenant collection that can only grow: `insert_one`, `find`, `count`. No update, no delete."""

    __slots__ = ("_collection",)

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    @property
    def collection_name(self) -> str:
        return self._collection.name

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        prepared = scoping.scoped_document(document, current_tenant().org_id)
        result = await self._collection.insert_one(prepared, **session_kwargs())
        return result.inserted_id

    async def find(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        sort: Sequence[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[Document]:
        scoped = scoping.scoped_filter(query, current_tenant().org_id)
        cursor = self._collection.find(scoped, sort=sort, skip=skip, limit=limit, **session_kwargs())
        return await cursor.to_list()

    async def count(self, query: Mapping[str, Any] | None = None) -> int:
        scoped = scoping.scoped_filter(query, current_tenant().org_id)
        return await self._collection.count_documents(scoped, **session_kwargs())
