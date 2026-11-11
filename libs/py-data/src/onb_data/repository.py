"""Repositories: the only way application code reads or writes MongoDB (PRD §7.9 Data access).

`TenantRepository` scopes every operation to the org in the current `TenantContext` and raises
`NoTenantContextError` before reaching the driver when there is none. `GlobalRepository` serves the short
list of global collections (`store.GLOBAL_COLLECTIONS`) unscoped. Neither exposes its collection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import scoping
from .context import current_tenant
from .errors import NotFoundError
from .session import session_kwargs

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

__all__ = ["GlobalRepository", "TenantRepository", "UpdateOutcome"]

Document = dict[str, Any]
SortSpec = Sequence[tuple[str, int]]


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    matched: int
    modified: int
    upserted_id: Any = None


class _Repository(ABC):
    """The operations. Subclasses decide how a filter, document, update and pipeline are scoped."""

    __slots__ = ("_collection",)

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    @property
    def collection_name(self) -> str:
        return self._collection.name

    @abstractmethod
    def _filter(self, query: Mapping[str, Any] | None) -> Document:
        """The filter sent to the driver."""

    @abstractmethod
    def _document(self, document: Mapping[str, Any]) -> Document:
        """The document inserted."""

    @abstractmethod
    def _update(self, update: Mapping[str, Any], *, upsert: bool) -> Document:
        """The update sent to the driver."""

    @abstractmethod
    def _pipeline(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        """The pipeline sent to the driver."""

    @staticmethod
    def _by_id(doc_id: Any) -> Document:
        """`{_id: doc_id}` for one concrete id. An operator or a collection would match other documents."""
        if isinstance(doc_id, Mapping | list | tuple | set | frozenset):
            raise TypeError("an id must be a single value, not an operator or a collection")
        return {"_id": doc_id}

    def _not_found(self) -> NotFoundError:
        return NotFoundError(f"no {self.collection_name} document with that id")

    async def find(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        sort: SortSpec | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[Document]:
        cursor = self._collection.find(
            self._filter(query), projection=projection, sort=sort, skip=skip, limit=limit, **session_kwargs()
        )
        return await cursor.to_list()

    async def find_one(
        self, query: Mapping[str, Any] | None = None, *, projection: Mapping[str, Any] | None = None
    ) -> Document | None:
        return await self._collection.find_one(self._filter(query), projection=projection, **session_kwargs())

    async def get(self, doc_id: Any, *, projection: Mapping[str, Any] | None = None) -> Document:
        """The document with this id, looked up as `{_id, org_id}`; `NotFoundError` otherwise."""
        found = await self.find_one(self._by_id(doc_id), projection=projection)
        if found is None:
            raise self._not_found()
        return found

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        result = await self._collection.insert_one(self._document(document), **session_kwargs())
        return result.inserted_id

    async def insert_many(self, documents: Sequence[Mapping[str, Any]]) -> list[Any]:
        if not isinstance(documents, list | tuple):
            raise TypeError("insert_many needs a list of documents")
        if not documents:
            raise ValueError("insert_many needs at least one document")
        prepared = [self._document(document) for document in documents]
        result = await self._collection.insert_many(prepared, **session_kwargs())
        return list(result.inserted_ids)

    async def update_one(
        self, query: Mapping[str, Any] | None, update: Mapping[str, Any], *, upsert: bool = False
    ) -> UpdateOutcome:
        scoped_query = self._filter(query)
        result = await self._collection.update_one(
            scoped_query, self._update(update, upsert=upsert), upsert=upsert, **session_kwargs()
        )
        return UpdateOutcome(result.matched_count, result.modified_count, result.upserted_id)

    async def update_many(self, query: Mapping[str, Any] | None, update: Mapping[str, Any]) -> UpdateOutcome:
        scoped_query = self._filter(query)
        result = await self._collection.update_many(
            scoped_query, self._update(update, upsert=False), **session_kwargs()
        )
        return UpdateOutcome(result.matched_count, result.modified_count)

    async def update_by_id(self, doc_id: Any, update: Mapping[str, Any]) -> UpdateOutcome:
        outcome = await self.update_one(self._by_id(doc_id), update)
        if outcome.matched == 0:
            raise self._not_found()
        return outcome

    async def delete_one(self, query: Mapping[str, Any] | None) -> int:
        result = await self._collection.delete_one(self._filter(query), **session_kwargs())
        return result.deleted_count

    async def delete_many(self, query: Mapping[str, Any] | None) -> int:
        """Delete every matching document (of the current org, on a tenant collection); the count."""
        result = await self._collection.delete_many(self._filter(query), **session_kwargs())
        return result.deleted_count

    async def delete_by_id(self, doc_id: Any) -> None:
        if await self.delete_one(self._by_id(doc_id)) == 0:
            raise self._not_found()

    async def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        cursor = await self._collection.aggregate(self._pipeline(pipeline), **session_kwargs())
        return await cursor.to_list()

    async def count(self, query: Mapping[str, Any] | None = None) -> int:
        return await self._collection.count_documents(self._filter(query), **session_kwargs())


class TenantRepository(_Repository):
    """A tenant collection seen through the current org only."""

    __slots__ = ()

    def _filter(self, query: Mapping[str, Any] | None) -> Document:
        return scoping.scoped_filter(query, current_tenant().org_id)

    def _document(self, document: Mapping[str, Any]) -> Document:
        return scoping.scoped_document(document, current_tenant().org_id)

    def _update(self, update: Mapping[str, Any], *, upsert: bool) -> Document:
        return scoping.scoped_update(update, current_tenant().org_id, upsert=upsert)

    def _pipeline(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        return scoping.scoped_pipeline(pipeline, current_tenant().org_id)


class GlobalRepository(_Repository):
    """One of the global collections (`store.GLOBAL_COLLECTIONS`): no org scope."""

    __slots__ = ()

    def _filter(self, query: Mapping[str, Any] | None) -> Document:
        return scoping.plain_filter(query)

    def _document(self, document: Mapping[str, Any]) -> Document:
        return scoping.plain_document(document)

    def _update(self, update: Mapping[str, Any], *, upsert: bool) -> Document:
        return scoping.check_update(update, tenant=False)

    def _pipeline(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        return scoping.check_pipeline(pipeline)
