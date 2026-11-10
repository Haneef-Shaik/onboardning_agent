"""An in-memory stand-in for T-1's `DataStore`, for unit tests of the fixture loader, tools and runner.

Scoping uses `onb_data.scoping` itself, so tenant rules match the real repository. Query matching covers
what the harness sends: equality on dotted paths (array membership included), `$in`, `$nin`, `$ne`,
`$exists`, `$regex`, `$gt`, `$gte`, `$lt`, `$lte`. Updates support `$set`. Real query semantics are
covered by the integration tests against the compose Mongo.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from itertools import count
from typing import Any, cast

from onb_data import GLOBAL_COLLECTIONS, DataStore, UpdateOutcome, current_tenant
from onb_data.scoping import plain_document, plain_filter, scoped_document, scoped_filter, scoped_update
from onb_evals.paths import MISSING, resolve
from pymongo.errors import DuplicateKeyError

Document = dict[str, Any]
_ids = count(1)


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "$in":
        return any(_equals(actual, e) for e in expected)
    if op == "$nin":
        return not any(_equals(actual, e) for e in expected)
    if op == "$ne":
        return not _equals(actual, expected)
    if op == "$exists":
        return (actual is not MISSING) is bool(expected)
    if op == "$regex":
        return isinstance(actual, str) and re.search(expected, actual) is not None
    if actual is MISSING or actual is None:
        return False
    comparisons = {
        "$gt": actual > expected,
        "$gte": actual >= expected,
        "$lt": actual < expected,
        "$lte": actual <= expected,
    }
    if op not in comparisons:
        raise ValueError(f"memory store: unsupported operator {op}")
    return bool(comparisons[op])


def _equals(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return bool(actual == expected)


def matches(document: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    for key, expected in query.items():
        if key.startswith("$"):
            raise ValueError(f"memory store: unsupported top-level operator {key}")
        actual = resolve(document, key)
        if isinstance(expected, Mapping) and expected and all(k.startswith("$") for k in expected):
            if not all(_compare(op, actual, value) for op, value in expected.items()):
                return False
        elif not _equals(actual, expected):
            return False
    return True


def _set_path(document: Document, path: str, value: Any) -> None:
    *parents, last = path.split(".")
    node = document
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = copy.deepcopy(value)


def _sort_key(path: str) -> Callable[[Document], tuple[bool, str]]:
    return lambda document: (resolve(document, path) is MISSING, str(resolve(document, path)))


class MemoryRepository:
    def __init__(self, name: str, store: MemoryStore, *, tenant: bool) -> None:
        self.collection_name = name
        self._store = store
        self._tenant = tenant

    @property
    def _docs(self) -> list[Document]:
        return self._store.data.setdefault(self.collection_name, [])

    def _filter(self, query: Mapping[str, Any] | None) -> Document:
        return scoped_filter(query, current_tenant().org_id) if self._tenant else plain_filter(query)

    async def find(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[Document]:
        scoped = self._filter(query)
        found = [copy.deepcopy(d) for d in self._docs if matches(d, scoped)]
        for key, direction in reversed(list(sort or [])):
            found.sort(key=_sort_key(key), reverse=direction < 0)
        found = found[skip:]
        return found[:limit] if limit else found

    async def find_one(self, query: Mapping[str, Any] | None = None, **_: Any) -> Document | None:
        found = await self.find(query, limit=1)
        return found[0] if found else None

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        prepared = scoped_document(document, current_tenant().org_id) if self._tenant else plain_document(document)
        prepared.setdefault("_id", f"mem_{next(_ids)}")
        self._store.check_unique(self.collection_name, prepared)
        self._docs.append(copy.deepcopy(prepared))
        return prepared["_id"]

    async def insert_many(self, documents: Sequence[Mapping[str, Any]]) -> list[Any]:
        return [await self.insert_one(d) for d in documents]

    async def _update(self, query: Mapping[str, Any] | None, update: Mapping[str, Any], *, many: bool) -> UpdateOutcome:
        scoped = self._filter(query)
        checked = scoped_update(update, current_tenant().org_id, upsert=False) if self._tenant else dict(update)
        if set(checked) != {"$set"}:
            raise ValueError("memory store: only $set updates")
        hits = [d for d in self._docs if matches(d, scoped)]
        hits = hits if many else hits[:1]
        for document in hits:
            for path, value in checked["$set"].items():
                _set_path(document, path, value)
        return UpdateOutcome(len(hits), len(hits))

    async def update_one(self, query: Mapping[str, Any] | None, update: Mapping[str, Any], **_: Any) -> UpdateOutcome:
        return await self._update(query, update, many=False)

    async def update_many(self, query: Mapping[str, Any] | None, update: Mapping[str, Any]) -> UpdateOutcome:
        return await self._update(query, update, many=True)

    async def delete_one(self, query: Mapping[str, Any] | None) -> int:
        scoped = self._filter(query)
        for index, document in enumerate(self._docs):
            if matches(document, scoped):
                del self._docs[index]
                return 1
        return 0

    async def count(self, query: Mapping[str, Any] | None = None) -> int:
        return len(await self.find(query))


class MemoryStore:
    """`tenant()`, `shared()` and `ensure_index()` like `DataStore`; unique indexes are enforced."""

    def __init__(self) -> None:
        self.data: dict[str, list[Document]] = {}
        self.unique: dict[str, list[tuple[str, ...]]] = {}
        self.closed = False

    def tenant(self, collection: str) -> MemoryRepository:
        assert collection not in GLOBAL_COLLECTIONS, collection
        return MemoryRepository(collection, self, tenant=True)

    def shared(self, collection: str) -> MemoryRepository:
        assert collection in GLOBAL_COLLECTIONS, collection
        return MemoryRepository(collection, self, tenant=False)

    async def ensure_index(
        self, collection: str, keys: Sequence[tuple[str, Any]], *, unique: bool = False, name: str | None = None
    ) -> str:
        if unique:
            self.unique.setdefault(collection, []).append(tuple(k for k, _ in keys))
        return name or "_".join(k for k, _ in keys)

    def check_unique(self, collection: str, document: Document) -> None:
        for fields in self.unique.get(collection, []):
            key = tuple(resolve(document, f) for f in fields)
            if any(tuple(resolve(d, f) for f in fields) == key for d in self.data.get(collection, [])):
                raise DuplicateKeyError(f"duplicate key on {collection} {fields}")

    def dump(self) -> str:
        return repr(self.data)

    async def close(self) -> None:
        self.closed = True


def as_data_store(store: MemoryStore) -> DataStore:
    """The memory store where code takes T-1's `DataStore` (it has the same repository surface)."""
    return cast("DataStore", store)
