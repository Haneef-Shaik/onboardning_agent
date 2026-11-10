"""An in-memory stand-in for the async PyMongo database under a real `DataStore`.

The real `TenantRepository` does the org scoping, so these unit tests see exactly what reaches the
driver. Matching is top-level equality only; real query semantics are in `tests_int/` against a
testcontainers MongoDB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from bson import ObjectId
from onb_data import DataStore

Document = dict[str, Any]


@dataclass
class FakeCursor:
    docs: list[Document]

    async def to_list(self, length: int | None = None) -> list[Document]:
        return list(self.docs)


def _matches(doc: Document, query: Document) -> bool:
    return all(doc.get(key) == value for key, value in query.items() if not key.startswith("$"))


@dataclass
class FakeCollection:
    name: str
    docs: list[Document] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def find(self, query: Document, **kwargs: Any) -> FakeCursor:
        self.calls.append(("find", {"filter": query, **kwargs}))
        found = [d for d in self.docs if _matches(d, query)]
        return FakeCursor(found[kwargs.get("skip", 0) :][: kwargs.get("limit") or None])

    async def count_documents(self, query: Document) -> int:
        self.calls.append(("count_documents", query))
        return sum(1 for d in self.docs if _matches(d, query))

    async def aggregate(self, pipeline: list[Document]) -> FakeCursor:
        self.calls.append(("aggregate", pipeline))
        org = pipeline[0]["$match"]
        return FakeCursor([d for d in self.docs if _matches(d, org)])

    async def insert_one(self, document: Document) -> SimpleNamespace:
        self.calls.append(("insert_one", document))
        stored = {"_id": ObjectId(), **document}
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(self, query: Document, update: Document, upsert: bool = False) -> SimpleNamespace:
        self.calls.append(("update_one", {"filter": query, "update": update}))
        hit = next((d for d in self.docs if _matches(d, query)), None)
        if hit is not None:
            hit.update(update.get("$set", {}))
        return SimpleNamespace(
            matched_count=int(hit is not None), modified_count=int(hit is not None), upserted_id=None
        )


@dataclass
class FakeDatabase:
    collections: dict[str, FakeCollection] = field(default_factory=dict)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))

    def calls(self) -> list[tuple[str, Any]]:
        return [call for coll in self.collections.values() for call in coll.calls]


def make_store(database: FakeDatabase) -> DataStore:
    return DataStore(database, None)  # type: ignore[arg-type]  # duck-typed driver stand-in
