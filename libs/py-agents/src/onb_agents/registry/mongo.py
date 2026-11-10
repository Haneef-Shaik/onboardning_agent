"""`MongoRegistryStore`: the registry in the global `agent_registry` and `tool_catalog` collections.

Through `onb_data` repositories only (`store.shared(...)`); the orgs to audit in come from `orgs`.
Version checks are part of the update filter, so a concurrent sync cannot overwrite a newer version.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from onb_data import DataStore
from pymongo.errors import DuplicateKeyError

from .sync import CATALOG_ID

__all__ = ["AGENT_REGISTRY", "ORGS", "TOOL_CATALOG", "MongoRegistryStore"]

AGENT_REGISTRY = "agent_registry"
TOOL_CATALOG = "tool_catalog"
ORGS = "orgs"

Document = dict[str, Any]


def _fields(document: Document) -> Document:
    return {key: value for key, value in document.items() if key != "_id"}


class MongoRegistryStore:
    __slots__ = ("_store",)

    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def org_ids(self) -> list[str]:
        return [str(org["_id"]) for org in await self._store.shared(ORGS).find({}, projection={"_id": 1})]

    async def agents(self) -> list[Document]:
        return await self._store.shared(AGENT_REGISTRY).find({}, sort=[("_id", 1)])

    async def agent(self, name: str) -> Document | None:
        return await self._store.shared(AGENT_REGISTRY).find_one({"_id": name})

    async def insert_agent(self, document: Document) -> None:
        await self._store.shared(AGENT_REGISTRY).insert_one(document)

    async def replace_agent(self, name: str, expected_version: int, document: Document) -> bool:
        outcome = await self._store.shared(AGENT_REGISTRY).update_one(
            {"_id": name, "version": expected_version}, {"$set": _fields(document)}
        )
        return outcome.matched == 1

    async def catalog(self) -> Document | None:
        return await self._store.shared(TOOL_CATALOG).find_one({"_id": CATALOG_ID})

    async def put_catalog(self, expected_version: int | None, document: Document) -> bool:
        repository = self._store.shared(TOOL_CATALOG)
        if expected_version is None:
            try:
                await repository.insert_one(document)
            except DuplicateKeyError:
                return False
            return True
        outcome = await repository.update_one(
            {"_id": CATALOG_ID, "version": expected_version}, {"$set": _fields(document)}
        )
        return outcome.matched == 1

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        return await self._store.run_transaction(callback)
