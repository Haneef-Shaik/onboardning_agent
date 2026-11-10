"""A small in-memory MongoDB double for the event bus unit tests.

It implements only what `onb_data` repositories call on behalf of the bus, with real semantics for
those calls: equality, `$in`, `$lte`, `$lt`, `$or` and `$exists` filters; `$set`, `$unset` and `$inc`
updates; a sort on one field; unique indexes (raising `DuplicateKeyError`); and transactions that
roll back every collection when the callback raises. Behaviour against a real MongoDB is proved in
`tests_int/`.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from onb_data import DataStore
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.database import AsyncDatabase

Document = dict[str, Any]


def _get(doc: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _matches_condition(present: bool, value: Any, condition: Any) -> bool:
    if isinstance(condition, Mapping) and condition and all(str(k).startswith("$") for k in condition):
        for op, operand in condition.items():
            if op == "$in":
                ok = present and value in operand
            elif op == "$lte":
                ok = present and value is not None and value <= operand
            elif op == "$lt":
                ok = present and value is not None and value < operand
            elif op == "$exists":
                ok = present is bool(operand)
            elif op == "$ne":
                ok = not present or value != operand
            else:
                raise NotImplementedError(op)
            if not ok:
                return False
        return True
    if condition is None:
        return not present or value is None
    return present and value == condition


def matches(doc: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(matches(doc, branch) for branch in condition):
                return False
            continue
        present, value = _get(doc, key)
        if not _matches_condition(present, value, condition):
            return False
    return True


def apply_update(doc: Document, update: Mapping[str, Any]) -> None:
    for op, fields in update.items():
        for key, value in fields.items():
            if op == "$set":
                doc[key] = copy.deepcopy(value)
            elif op == "$unset":
                doc.pop(key, None)
            elif op == "$inc":
                doc[key] = doc.get(key, 0) + value
            elif op == "$setOnInsert":
                continue
            else:
                raise NotImplementedError(op)


class _Cursor:
    def __init__(self, docs: list[Document]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[Document]:
        return copy.deepcopy(self._docs)


@dataclass
class MemoryCollection:
    name: str
    db: MemoryDatabase
    docs: list[Document] = field(default_factory=list)
    unique: list[list[str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def _check_unique(self, candidate: Document, ignore: Document | None = None) -> None:
        for fields in self.unique:
            key = [_get(candidate, f)[1] for f in fields]
            for doc in self.docs:
                if doc is not ignore and [_get(doc, f)[1] for f in fields] == key:
                    raise DuplicateKeyError(f"E11000 duplicate key on {self.name} {fields}")

    def _sorted(self, docs: list[Document], sort: Sequence[tuple[str, int]] | None) -> list[Document]:
        for key, direction in reversed(list(sort or [])):

            def by_key(doc: Document, field_name: str = key) -> Any:
                return _get(doc, field_name)[1]

            docs = sorted(docs, key=by_key, reverse=direction < 0)
        return docs

    async def insert_one(self, document: Document, **kwargs: Any) -> SimpleNamespace:
        self.calls.append("insert_one")
        await self.db.before_write()
        doc = copy.deepcopy(document)
        doc.setdefault("_id", f"oid-{self.db.next_id()}")
        self.unique_id_check(doc)
        self._check_unique(doc)
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def insert_many(self, documents: list[Document], **kwargs: Any) -> SimpleNamespace:
        ids = [(await self.insert_one(d, **kwargs)).inserted_id for d in documents]
        return SimpleNamespace(inserted_ids=ids)

    def unique_id_check(self, doc: Document) -> None:
        if any(existing["_id"] == doc["_id"] for existing in self.docs):
            raise DuplicateKeyError(f"E11000 duplicate _id on {self.name}")

    async def find_one(self, query: Document, projection: Any = None, **kwargs: Any) -> Document | None:
        self.calls.append("find_one")
        for doc in self.docs:
            if matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query: Document, **kwargs: Any) -> _Cursor:
        self.calls.append("find")
        found = self._sorted([d for d in self.docs if matches(d, query)], kwargs.get("sort"))
        limit = kwargs.get("limit") or len(found)
        return _Cursor(found[:limit])

    async def count_documents(self, query: Document, **kwargs: Any) -> int:
        return sum(1 for d in self.docs if matches(d, query))

    async def find_one_and_update(
        self,
        query: Document,
        update: Document,
        *,
        sort: Sequence[tuple[str, int]] | None = None,
        return_document: Any = ReturnDocument.BEFORE,
        **kwargs: Any,
    ) -> Document | None:
        self.calls.append("find_one_and_update")
        await self.db.before_write()
        candidates = self._sorted([d for d in self.docs if matches(d, query)], sort)
        if not candidates:
            return None
        target = candidates[0]
        before = copy.deepcopy(target)
        apply_update(target, update)
        return copy.deepcopy(target) if return_document == ReturnDocument.AFTER else before

    async def update_one(self, query: Document, update: Document, upsert: bool = False, **kwargs: Any) -> Any:
        self.calls.append("update_one")
        await self.db.before_write()
        for doc in self.docs:
            if matches(doc, query):
                apply_update(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def delete_one(self, query: Document, **kwargs: Any) -> SimpleNamespace:
        self.calls.append("delete_one")
        await self.db.before_write()
        for i, doc in enumerate(self.docs):
            if matches(doc, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def create_index(self, keys: list[tuple[str, Any]], *, unique: bool = False, **kwargs: Any) -> str:
        self.db.indexes.append((self.name, list(keys), unique))
        if unique:
            self.unique.append([k for k, _ in keys])
        return str(kwargs.get("name") or "_".join(f"{k}_{d}" for k, d in keys))


@dataclass
class MemoryDatabase:
    collections: dict[str, MemoryCollection] = field(default_factory=dict)
    indexes: list[tuple[str, list[tuple[str, Any]], bool]] = field(default_factory=list)
    # Called before every write; a test can make it raise to simulate a failure mid-transaction.
    write_hook: Callable[[], Awaitable[None]] | None = None
    _ids: int = 0

    def __getitem__(self, name: str) -> MemoryCollection:
        if name not in self.collections:
            self.collections[name] = MemoryCollection(name=name, db=self)
        return self.collections[name]

    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    async def before_write(self) -> None:
        if self.write_hook is not None:
            await self.write_hook()

    def snapshot(self) -> dict[str, list[Document]]:
        return {name: copy.deepcopy(c.docs) for name, c in self.collections.items()}

    def restore(self, snap: dict[str, list[Document]]) -> None:
        for name, coll in self.collections.items():
            coll.docs = snap.get(name, [])

    async def command(self, command: Any) -> dict[str, Any]:
        return {"ok": 1.0}


@dataclass
class MemorySession:
    db: MemoryDatabase
    commits: int = 0
    aborts: int = 0

    async def __aenter__(self) -> MemorySession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def with_transaction(self, callback: Callable[[MemorySession], Awaitable[Any]]) -> Any:
        snap = self.db.snapshot()
        try:
            result = await callback(self)
        except BaseException:
            self.db.restore(snap)
            self.aborts += 1
            raise
        self.commits += 1
        return result


@dataclass
class MemoryClient:
    db: MemoryDatabase
    sessions: list[MemorySession] = field(default_factory=list)

    def start_session(self) -> MemorySession:
        session = MemorySession(self.db)
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        return None


def memory_store(db: MemoryDatabase | None = None) -> tuple[DataStore, MemoryDatabase, MemoryClient]:
    """A DataStore over the in-memory double. These casts are where it stands in for driver types."""
    database = db if db is not None else MemoryDatabase()
    client = MemoryClient(database)
    store = DataStore._wrap(
        cast("AsyncDatabase[Document]", database), cast("AsyncMongoClient[Document] | None", client)
    )
    return store, database, client
