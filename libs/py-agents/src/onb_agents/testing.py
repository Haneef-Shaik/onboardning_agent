"""Test doubles for the agent registry. Test-only: production code never imports it.

`InMemoryRegistryStore` keeps `MongoRegistryStore`'s contract: version-checked replaces, a catalog
insert that loses to an existing one, and `atomically` that restores the registry when the callback
raises. Documents are copied in and out, so callers never share state with the store.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from onb_agents.registry.sync import CATALOG_ID

__all__ = ["InMemoryRegistryStore"]

Document = dict[str, Any]


class InMemoryRegistryStore:
    def __init__(self, org_ids: tuple[str, ...] = ("org_acme", "org_globex")) -> None:
        self.orgs = org_ids
        self._agents: dict[str, Document] = {}
        self._catalog: Document | None = None
        self.transactions = 0

    async def org_ids(self) -> list[str]:
        return list(self.orgs)

    async def agents(self) -> list[Document]:
        return [copy.deepcopy(self._agents[name]) for name in sorted(self._agents)]

    async def agent(self, name: str) -> Document | None:
        found = self._agents.get(name)
        return None if found is None else copy.deepcopy(found)

    async def insert_agent(self, document: Document) -> None:
        name = str(document["_id"])
        if name in self._agents:
            raise KeyError(f"duplicate key {name}")
        self._agents = {**self._agents, name: copy.deepcopy(document)}

    async def replace_agent(self, name: str, expected_version: int, document: Document) -> bool:
        current = self._agents.get(name)
        if current is None or current["version"] != expected_version:
            return False
        self._agents = {**self._agents, name: copy.deepcopy(document)}
        return True

    async def catalog(self) -> Document | None:
        return copy.deepcopy(self._catalog)

    async def put_catalog(self, expected_version: int | None, document: Document) -> bool:
        current = self._catalog
        if (current is None) != (expected_version is None):
            return False
        if current is not None and current["version"] != expected_version:
            return False
        self._catalog = copy.deepcopy({**document, "_id": CATALOG_ID})
        return True

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        self.transactions += 1
        agents, catalog = self._agents, self._catalog
        try:
            return await callback()
        except BaseException:
            self._agents, self._catalog = agents, catalog
            raise

    def tamper(self, name: str, manifest: Document) -> None:
        """Overwrite a stored manifest behind the registry's back (a forged record)."""
        self._agents = {**self._agents, name: {**self._agents[name], "manifest": manifest}}
