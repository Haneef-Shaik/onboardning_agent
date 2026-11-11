"""The async PyMongo client factory and the store that hands out repositories (PRD §7.9).

`connect()` is the only place a MongoDB client is made. The store keeps the client and database
private and hands out repositories only, so no caller ever holds a collection or database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from pymongo import AsyncMongoClient as _AsyncMongoClient

from .append_only import APPEND_ONLY_COLLECTIONS, AppendOnlyRepository
from .errors import InvalidIndexError, TransactionError, UnknownCollectionError
from .queue import QUEUE_COLLECTIONS, QueueRepository
from .repository import GlobalRepository, TenantRepository
from .scoping import ORG_FIELD, IndexKey, validate_index_keys
from .session import bound_session, in_transaction

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

__all__ = ["GLOBAL_COLLECTIONS", "DataStore", "connect"]

Document = dict[str, Any]

# The only collections without tenant scoping. Everything else is a tenant collection.
# `user_logins` is the login directory (ADR 0004): `{_id: sha256(email), org_id, user_id}` and nothing
# else, read before any org is known; the user records themselves are tenant documents in `users`.
GLOBAL_COLLECTIONS = frozenset({"orgs", "agent_registry", "tool_catalog", "user_logins"})

_BAD_DATABASE_CHARS = frozenset('/\\. "$\x00')


def _collection_name(name: object) -> str:
    if not isinstance(name, str) or not name or "$" in name or "\x00" in name or name.startswith("system."):
        raise ValueError(f"invalid collection name: {name!r}")
    return name


def _database_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > 63 or _BAD_DATABASE_CHARS & set(name):
        raise ValueError(f"invalid database name: {name!r}")
    return name


_EXISTS: Final = {"$exists": True}


def _partial_filter(partial: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(partial, Mapping) or not partial:
        raise InvalidIndexError("a partial filter names at least one field")
    for field, condition in partial.items():
        if not isinstance(field, str) or not field or field.startswith("$") or field == ORG_FIELD:
            raise InvalidIndexError("a partial filter names plain fields other than org_id")
        if condition != _EXISTS:
            raise InvalidIndexError('a partial filter only takes {field: {"$exists": True}}')
    return {field: dict(_EXISTS) for field in partial}


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
        if name in APPEND_ONLY_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is append-only: use append_only({name!r})")
        return TenantRepository(self._database[name])

    def append_only(self, collection: str) -> AppendOnlyRepository:
        """The insert-and-read view of an append-only tenant collection (`audit_log`); no update or delete."""
        name = _collection_name(collection)
        if name not in APPEND_ONLY_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is not an append-only collection: use tenant({name!r})")
        return AppendOnlyRepository(self._database[name])

    def queue(self, collection: str) -> QueueRepository:
        """The cross-org worker view of `jobs` or `dead_letters` (see `onb_data.queue`)."""
        name = _collection_name(collection)
        if name not in QUEUE_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is not a queue collection: use tenant({name!r})")
        return QueueRepository(self._database[name])

    async def run_transaction[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        """Run `callback` in one transaction and return its result.

        Every repository call inside it joins the transaction. It commits when the callback returns
        and rolls back when it raises (the error propagates). The driver retries the whole callback
        on a transient error, such as a write conflict, so the callback must be safe to run again.
        """
        if in_transaction():
            raise TransactionError("transactions do not nest: call the inner work inside the outer callback")
        if self._client is None:
            raise TransactionError("this store has no client to start a session with")

        async def attempt(session: Any) -> T:
            with bound_session(session):
                return await callback()

        async with self._client.start_session() as session:
            return await session.with_transaction(attempt)

    def shared(self, collection: str) -> GlobalRepository:
        name = _collection_name(collection)
        if name not in GLOBAL_COLLECTIONS:
            raise UnknownCollectionError(f"{name} is a tenant collection: use tenant({name!r})")
        return GlobalRepository(self._database[name])

    async def ensure_index(
        self,
        collection: str,
        keys: Sequence[IndexKey],
        *,
        unique: bool = False,
        name: str | None = None,
        partial: Mapping[str, Any] | None = None,
    ) -> str:
        """Create an index. On a tenant collection the keys must start with `org_id`.

        Queue collections (`jobs`, `dead_letters`) are claimed across orgs, so a non-unique index
        there may lead with another field (`status`); a unique one must still start with `org_id`.

        `partial` limits the index to documents that have the named fields, as
        `{field: {"$exists": True}, ...}` and nothing else (never `org_id`), so a unique index can
        skip records that lack the field without ever being narrowed by org.
        """
        coll = _collection_name(collection)
        if coll in APPEND_ONLY_COLLECTIONS and unique:
            raise InvalidIndexError("no unique index on an append-only collection: it would refuse records")
        queue_lookup = coll in QUEUE_COLLECTIONS and not unique
        spec = validate_index_keys(keys, tenant=coll not in GLOBAL_COLLECTIONS and not queue_lookup)
        if coll in QUEUE_COLLECTIONS and unique and spec[0][0] != ORG_FIELD:
            raise InvalidIndexError("a unique index on a queue collection must start with org_id")
        options: dict[str, Any] = {"unique": unique}
        if partial is not None:
            options["partialFilterExpression"] = _partial_filter(partial)
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
