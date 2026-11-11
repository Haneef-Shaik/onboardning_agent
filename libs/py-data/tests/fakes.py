"""An in-memory stand-in for the async PyMongo collection/database the repository drives.

It records each driver call so unit tests can assert exactly which filter, document, update or
pipeline reached the driver. Real query semantics are covered by the integration tests in
`tests_int/`, which run against the compose Mongo.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from onb_data import DataStore

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.database import AsyncDatabase

Document = dict[str, Any]


@dataclass
class Call:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeCursor:
    def __init__(self, docs: list[Document]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[Document]:
        return [dict(doc) for doc in self._docs]


@dataclass
class FakeCollection:
    name: str = "hires"
    docs: list[Document] = field(default_factory=list)
    find_one_result: Document | None = None
    find_one_and_update_result: Document | None = None
    matched_count: int = 1
    deleted_count: int = 1
    count_result: int = 0
    calls: list[Call] = field(default_factory=list)

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(Call(method, args, kwargs))

    def last(self, method: str) -> Call:
        matching = [call for call in self.calls if call.method == method]
        assert matching, f"no {method} call recorded; calls were {[c.method for c in self.calls]}"
        return matching[-1]

    def find(self, *args: Any, **kwargs: Any) -> FakeCursor:
        self._record("find", *args, **kwargs)
        return FakeCursor(self.docs)

    async def find_one(self, *args: Any, **kwargs: Any) -> Document | None:
        self._record("find_one", *args, **kwargs)
        return None if self.find_one_result is None else dict(self.find_one_result)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Document | None:
        self._record("find_one_and_update", *args, **kwargs)
        result = self.find_one_and_update_result
        return None if result is None else dict(result)

    async def insert_one(self, document: Document, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._record("insert_one", document, *args, **kwargs)
        return SimpleNamespace(inserted_id=document.get("_id", "generated-id"))

    async def insert_many(self, documents: list[Document], *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._record("insert_many", documents, *args, **kwargs)
        return SimpleNamespace(inserted_ids=[doc.get("_id", f"generated-{i}") for i, doc in enumerate(documents)])

    async def _update(self, method: str, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._record(method, *args, **kwargs)
        upserted = "upserted-id" if kwargs.get("upsert") and self.matched_count == 0 else None
        return SimpleNamespace(
            matched_count=self.matched_count, modified_count=self.matched_count, upserted_id=upserted
        )

    async def update_one(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return await self._update("update_one", *args, **kwargs)

    async def update_many(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return await self._update("update_many", *args, **kwargs)

    async def delete_one(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._record("delete_one", *args, **kwargs)
        return SimpleNamespace(deleted_count=self.deleted_count)

    async def delete_many(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._record("delete_many", *args, **kwargs)
        return SimpleNamespace(deleted_count=self.deleted_count)

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        self._record("count_documents", *args, **kwargs)
        return self.count_result

    async def aggregate(self, *args: Any, **kwargs: Any) -> FakeCursor:
        self._record("aggregate", *args, **kwargs)
        return FakeCursor(self.docs)

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        self._record("create_index", *args, **kwargs)
        return str(kwargs.get("name") or "_".join(f"{key}_{direction}" for key, direction in args[0]))


@dataclass
class FakeDatabase:
    collections: dict[str, FakeCollection] = field(default_factory=dict)
    commands: list[Any] = field(default_factory=list)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name=name))

    async def command(self, command: Any) -> dict[str, Any]:
        self.commands.append(command)
        return {"ok": 1.0}


@dataclass
class FakeSession:
    """Runs a `with_transaction` callback once; counts transactions and aborts."""

    transactions: int = 0
    aborted: int = 0
    ended: bool = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.ended = True

    async def with_transaction(self, callback: Callable[[FakeSession], Awaitable[Any]]) -> Any:
        self.transactions += 1
        try:
            return await callback(self)
        except BaseException:
            self.aborted += 1
            raise


@dataclass
class FakeClient:
    closed: bool = False
    sessions: list[FakeSession] = field(default_factory=list)

    def start_session(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        self.closed = True


def make_store(database: FakeDatabase | None = None, client: FakeClient | None = None) -> DataStore:
    """A DataStore over the fakes. These casts are the only place a fake stands in for a driver type."""
    db = database if database is not None else FakeDatabase()
    return DataStore._wrap(
        cast("AsyncDatabase[Document]", db),
        cast("AsyncMongoClient[Document] | None", client),
    )
