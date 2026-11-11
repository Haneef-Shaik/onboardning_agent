"""Test support: an in-memory stand-in for the PyMongo async database, under a real `DataStore`.

Test-only, like `onb_api.testing`. `memory_store()` wraps it in T-1's `DataStore`, so tenant scoping,
the vault, the keyring's key store and the hire store all run their production code; only the driver
is replaced. It supports the subset of MongoDB those use: equality on dotted paths, `$exists`, `$in`,
`$ne`, `$lt`, a top-level `$or`; sort, skip, limit, projection by inclusion; `$set`, `$unset`,
`$setOnInsert`; unique indexes, partial ones included, refused with `DuplicateKeyError` like the server.

`raw(collection)` returns copies of every stored document, across orgs, for scan tests.
"""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from bson import ObjectId
from onb_crypto import ORG_KEYS_COLLECTION, Keyring, MasterKey, RepositoryOrgKeyStore, SecretKey
from onb_data import DataStore
from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

__all__ = ["MemoryCollection", "MemoryDatabase", "memory_keyring", "memory_store"]

Document = dict[str, Any]
_MISSING = object()


def _get(document: Mapping[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _set(document: Document, path: str, value: Any) -> None:
    *parents, last = path.split(".")
    node = document
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = copy.deepcopy(value)


def _unset(document: Document, path: str) -> None:
    *parents, last = path.split(".")
    node: Any = document
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(last, None)


def _condition_holds(value: Any, condition: Any) -> bool:
    if isinstance(condition, Mapping) and condition and all(str(k).startswith("$") for k in condition):
        for op, operand in condition.items():
            if op == "$exists":
                if (value is not _MISSING) != bool(operand):
                    return False
            elif op == "$in":
                if (None if value is _MISSING else value) not in list(operand):
                    return False
            elif op == "$lt":
                if value is _MISSING or value is None or not value < operand:
                    return False
            elif op == "$ne":
                if (None if value is _MISSING else value) == operand:
                    return False
            else:
                raise NotImplementedError(f"the in-memory database does not support {op}")
        return True
    equal: bool = (None if value is _MISSING else value) == condition
    return equal


def matches(document: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    for path, condition in query.items():
        if path == "$or":
            if not any(matches(document, branch) for branch in condition):
                return False
        elif not _condition_holds(_get(document, path), condition):
            return False
    return True


def _sort_value(value: Any) -> tuple[int, Any]:
    return (0, None) if value is _MISSING or value is None else (1, value)


def _sort_key(path: str) -> Callable[[Mapping[str, Any]], tuple[int, Any]]:
    return lambda document: _sort_value(_get(document, path))


@dataclass
class _Index:
    keys: tuple[str, ...]
    unique: bool
    partial: Mapping[str, Any] | None
    name: str


@dataclass
class _Result:
    inserted_id: Any = None
    inserted_ids: list[Any] = field(default_factory=list)
    matched_count: int = 0
    modified_count: int = 0
    upserted_id: Any = None
    deleted_count: int = 0


class _Cursor:
    def __init__(self, documents: list[Document]) -> None:
        self._documents = documents

    async def to_list(self, length: int | None = None) -> list[Document]:
        return self._documents if length is None else self._documents[:length]


class MemoryCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.documents: list[Document] = []
        self.indexes: list[_Index] = []
        self.fail_next_insert: Exception | None = None

    # --- indexes -----------------------------------------------------------------------------------

    async def create_index(self, keys: Sequence[tuple[str, Any]], **options: Any) -> str:
        fields = tuple(k for k, _ in keys)
        name = options.get("name") or "_".join(f"{k}_{d}" for k, d in keys)
        index = _Index(fields, bool(options.get("unique")), options.get("partialFilterExpression"), name)
        if index.unique:
            self._check_unique(index, self.documents)
        self.indexes = [*(i for i in self.indexes if i.name != name), index]
        return name

    def _check_unique(self, index: _Index, documents: Iterable[Document]) -> None:
        seen: set[str] = set()
        for document in documents:
            if index.partial is not None and not matches(document, index.partial):
                continue
            key = repr(tuple(_sort_value(_get(document, k)) for k in index.keys))
            if key in seen:
                raise DuplicateKeyError(f"E11000 duplicate key error index: {index.name}")
            seen.add(key)

    def _check_all_unique(self, documents: list[Document]) -> None:
        for index in self.indexes:
            if index.unique:
                self._check_unique(index, documents)
        ids = [d["_id"] for d in documents]
        if len(set(map(repr, ids))) != len(ids):
            raise DuplicateKeyError("E11000 duplicate key error index: _id_")

    # --- reads -------------------------------------------------------------------------------------

    def _found(self, query: Mapping[str, Any] | None) -> list[Document]:
        return [d for d in self.documents if matches(d, query or {})]

    @staticmethod
    def _project(document: Document, projection: Mapping[str, Any] | None) -> Document:
        if not projection:
            return copy.deepcopy(document)
        kept = {k for k, v in projection.items() if v}
        return {k: copy.deepcopy(v) for k, v in document.items() if k in kept or k == "_id"}

    def find(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
        **_: Any,
    ) -> _Cursor:
        found = self._found(query)
        for key, direction in reversed(list(sort or [])):
            found = sorted(found, key=_sort_key(key), reverse=direction < 0)
        found = found[skip:]
        if limit:
            found = found[:limit]
        return _Cursor([self._project(d, projection) for d in found])

    async def find_one(
        self, query: Mapping[str, Any] | None = None, *, projection: Mapping[str, Any] | None = None, **_: Any
    ) -> Document | None:
        found = self._found(query)
        return self._project(found[0], projection) if found else None

    async def count_documents(self, query: Mapping[str, Any] | None = None, **_: Any) -> int:
        return len(self._found(query))

    # --- writes ------------------------------------------------------------------------------------

    async def insert_one(self, document: Mapping[str, Any], **_: Any) -> _Result:
        result = await self.insert_many([document])
        return _Result(inserted_id=result.inserted_ids[0])

    async def insert_many(self, documents: Sequence[Mapping[str, Any]], **_: Any) -> _Result:
        if self.fail_next_insert is not None:
            error, self.fail_next_insert = self.fail_next_insert, None
            raise error
        added = [{"_id": ObjectId(), **copy.deepcopy(dict(d))} for d in documents]
        candidate = [*self.documents, *added]
        self._check_all_unique(candidate)
        self.documents = candidate
        return _Result(inserted_ids=[d["_id"] for d in added])

    async def update_one(
        self, query: Mapping[str, Any], update: Mapping[str, Any], *, upsert: bool = False, **_: Any
    ) -> _Result:
        for position, existing in enumerate(self.documents):
            if matches(existing, query):
                changed = self._apply(copy.deepcopy(existing), update, inserting=False)
                candidate = [*self.documents[:position], changed, *self.documents[position + 1 :]]
                self._check_all_unique(candidate)
                self.documents = candidate
                return _Result(matched_count=1, modified_count=int(changed != existing))
        if not upsert:
            return _Result()
        seed = {k: v for k, v in query.items() if not str(k).startswith("$") and not isinstance(v, Mapping)}
        created = self._apply({"_id": ObjectId(), **copy.deepcopy(seed)}, update, inserting=True)
        candidate = [*self.documents, created]
        self._check_all_unique(candidate)
        self.documents = candidate
        return _Result(upserted_id=created["_id"])

    async def update_many(self, query: Mapping[str, Any], update: Mapping[str, Any], **_: Any) -> _Result:
        changed = [
            self._apply(copy.deepcopy(d), update, inserting=False) if matches(d, query) else d for d in self.documents
        ]
        count = sum(1 for d in self.documents if matches(d, query))
        self._check_all_unique(changed)
        self.documents = changed
        return _Result(matched_count=count, modified_count=count)

    async def delete_one(self, query: Mapping[str, Any], **_: Any) -> _Result:
        for position, existing in enumerate(self.documents):
            if matches(existing, query):
                self.documents = [*self.documents[:position], *self.documents[position + 1 :]]
                return _Result(deleted_count=1)
        return _Result()

    async def delete_many(self, query: Mapping[str, Any], **_: Any) -> _Result:
        kept = [d for d in self.documents if not matches(d, query)]
        deleted = len(self.documents) - len(kept)
        self.documents = kept
        return _Result(deleted_count=deleted)

    @staticmethod
    def _apply(document: Document, update: Mapping[str, Any], *, inserting: bool) -> Document:
        for op, fields in update.items():
            if op == "$set" or (op == "$setOnInsert" and inserting):
                for path, value in fields.items():
                    _set(document, path, value)
            elif op == "$unset":
                for path in fields:
                    _unset(document, path)
            elif op != "$setOnInsert":
                raise NotImplementedError(f"the in-memory database does not support {op}")
        return document


class MemoryDatabase:
    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.collections: dict[str, MemoryCollection] = {}

    def __getitem__(self, name: str) -> MemoryCollection:
        if name not in self.collections:
            self.collections = {**self.collections, name: MemoryCollection(name)}
        return self.collections[name]

    async def command(self, name: str, *_: Any, **__: Any) -> Document:
        return {"ok": 1.0, "command": name}

    def raw(self, collection: str) -> list[Document]:
        """Every stored document of `collection`, in every org (copies)."""
        return copy.deepcopy(self[collection].documents)

    def all_documents(self) -> Iterable[tuple[str, Document]]:
        return itertools.chain.from_iterable(
            ((name, copy.deepcopy(d)) for d in coll.documents) for name, coll in self.collections.items()
        )


def memory_store(database: MemoryDatabase | None = None) -> DataStore:
    """A `DataStore` over an in-memory database. The cast is the only place the fake stands in for
    the driver's type (as in py-data's own tests)."""
    db = database if database is not None else MemoryDatabase()
    return DataStore(cast("AsyncDatabase[Document]", db), None)


def memory_keyring(store: DataStore) -> Keyring:
    """A keyring over `store`'s `org_keys`, under a master key made fresh for this test run."""
    return Keyring(MasterKey(SecretKey.generate()), RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
