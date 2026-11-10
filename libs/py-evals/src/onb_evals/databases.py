"""A throwaway MongoDB database per repetition (evals/README.md step 1: "a fresh fixture each time").

Each `fresh()` connects T-1's `DataStore` to a new database named `onb_eval_<random>` on the given
server and drops it when the block exits, also when the block fails. Data access inside goes through
the store's repositories; dropping a whole database is an admin command, so it uses a short-lived
client of its own, and refuses any name without the eval prefix (ADR 0003).
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Final, Protocol

from onb_data import DataStore, connect
from pymongo import AsyncMongoClient

__all__ = ["DATABASE_PREFIX", "ThrowawayDatabases"]

DATABASE_PREFIX: Final = "onb_eval_"


class _Admin(Protocol):
    async def drop_database(self, name: str) -> Any: ...

    async def close(self) -> None: ...


def _admin_client(uri: str) -> _Admin:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(uri, appname="onb-evals-admin")
    return client


class ThrowawayDatabases:
    def __init__(
        self,
        uri: str,
        *,
        connect: Callable[[str, str], DataStore] = connect,
        admin: Callable[[str], _Admin] = _admin_client,
    ) -> None:
        if not uri:
            raise ValueError("a MongoDB uri is required")
        self._uri = uri
        self._connect = connect
        self._admin = admin

    @asynccontextmanager
    async def fresh(self) -> AsyncIterator[DataStore]:
        name = f"{DATABASE_PREFIX}{secrets.token_hex(8)}"
        store = self._connect(self._uri, name)
        try:
            yield store
        finally:
            try:
                await self.drop(name)
            finally:
                await store.close()

    async def drop(self, name: str) -> None:
        if not name.startswith(DATABASE_PREFIX):
            raise ValueError(f"refusing to drop {name!r}: only databases with the {DATABASE_PREFIX} prefix")
        client = self._admin(self._uri)
        try:
            await client.drop_database(name)
        finally:
            await client.close()
