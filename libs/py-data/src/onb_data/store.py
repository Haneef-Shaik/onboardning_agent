"""The async PyMongo client factory and the store that hands out repositories (PRD §7.9).

`connect()` is the only place a MongoDB client is made. The store keeps the client and database
private and hands out repositories only, so no caller ever holds a collection or database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pymongo import AsyncMongoClient as _AsyncMongoClient

from .errors import UnknownCollectionError
from .repository import GlobalRepository, TenantRepository
from .scoping import IndexKey, validate_index_keys

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

__all__ = ["GLOBAL_COLLECTIONS", "DataStore", "connect"]

Document = dict[str, Any]

# The only collections without `org_id`. Everything else is a tenant collection.
GLOBAL_COLLECTIONS = frozenset({"orgs", "agent_registry", "tool_catalog"})

_BAD_DATABASE_CHARS = frozenset('/\\. "$\x00')


def _collection_name(name: object) -> str:
    if not isinstance(name, str) or not name or "$" in name or "\x00" in name or name.startswith("system."):
        raise ValueError(f"invalid collection name: {name!r}")
    return name


def _database_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > 63 or _BAD_DATABASE_CHARS & set(name):
        raise ValueError(f"invalid database name: {name!r}")
    return name


class DataStore:
    """Repositories over one database. Make one with `connect()`."""

    __slots__ = ("_client", "_database")

    def __init__(self, database: AsyncDatabase[Document], client: _AsyncMongoClient[Document] | None) -> None:
        self._database = database
        self._client = client

    @classmethod
    def _wrap(cls, database: AsyncDatabase[Document], client: _AsyncMongoClient[Document] | None) -> DataStore:
        return cls(database, client)

    def tenant(self, collection: str) -> TenantRepository:
        name = _collection_name(collection)
        if name in GLOBAL_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is a global collection: use shared({name!r})")
        return TenantRepository(self._database[name])

    def shared(self, collection: str) -> GlobalRepository:
        name = _collection_name(collection)
        if name not in GLOBAL_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is a tenant collection: use tenant({name!r})")
        return GlobalRepository(self._database[name])

    async def ensure_index(
        self, collection: str, keys: Sequence[IndexKey], *, unique: bool = False, name: str | None = None
    ) -> str:
        """Create an index. On a tenant collection the keys must start with `org_id`."""
        coll = _collection_name(collection)
        spec = validate_index_keys(keys, tenant=coll not in GLOBAL_COLLECTIONS)
        options: dict[str, Any] = {"unique": unique}
        if name is not None:
            options["name"] = name
        return await self._database[coll].create_index(spec, **options)

    async def ping(self) -> None:
        await self._database.command("ping")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


def connect(
    uri: str,
    database: str,
    *,
    app_name: str = "onboarding-platform",
    event_listeners: Sequence[Any] = (),
) -> DataStore:
    """A store over `database`. The URI (with credentials) comes from configuration, never code.

    `event_listeners` go to the PyMongo client (T-2 attaches its command listener here). The client
    connects lazily, on the first operation.
    """
    if not isinstance(uri, str) or not uri:
        raise ValueError("a MongoDB uri is required")
    name = _database_name(database)
    client: _AsyncMongoClient[Document] = _AsyncMongoClient(
        uri,
        appname=app_name,
        tz_aware=True,
        uuidRepresentation="standard",
        event_listeners=list(event_listeners),
    )
    return DataStore(client[name], client)
