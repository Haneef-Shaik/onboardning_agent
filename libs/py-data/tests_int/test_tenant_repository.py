"""Two-org tenant isolation against a real MongoDB (T-1 boxes 1 and 2; PRD §7.9).

Runs against the compose Mongo through the loopback tunnel from `conftest.py` (a throw-away
Docker MongoDB, PRD §7.5 "testcontainers / Docker"). Each test uses its own scratch database,
dropped afterwards. Everything is read back with the root client too, so the test does not trust
the repository to report on itself.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data import DataStore, NoTenantContextError, NotFound, TenantRepository, connect, tenant_scope
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

Client = MongoClient[dict[str, object]]
ORG_A = "org_alpha"
ORG_B = "org_beta"
COLLECTION = "hires"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    """(uri, database) for a fresh database seeded with two hires in each of two orgs."""
    name = f"it_t1_{uuid.uuid4().hex[:12]}"
    root_client[name][COLLECTION].insert_many(
        [
            {"_id": "a1", "org_id": ORG_A, "name": "Asha", "rank": 2},
            {"_id": "a2", "org_id": ORG_A, "name": "Arun", "rank": 1},
            {"_id": "b1", "org_id": ORG_B, "name": "Bina", "rank": 3},
            {"_id": "b2", "org_id": ORG_B, "name": "Bala", "rank": 0},
        ]
    )
    yield _uri(root_client), name
    root_client.drop_database(name)


def _uri(root_client: Client) -> str:
    """The root user's URI to the tunnel. `root_client` already proved the variables are set."""
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


def with_store[T](scratch: tuple[str, str], body: Callable[[DataStore], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        store = connect(scratch[0], scratch[1])
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(main())


OPERATIONS: dict[str, Callable[[TenantRepository], Awaitable[object]]] = {
    "find": lambda r: r.find(),
    "find_one": lambda r: r.find_one({"_id": "a1"}),
    "get": lambda r: r.get("a1"),
    "insert_one": lambda r: r.insert_one({"name": "new"}),
    "insert_many": lambda r: r.insert_many([{"name": "new"}]),
    "update_one": lambda r: r.update_one({"_id": "a1"}, {"$set": {"name": "x"}}),
    "update_many": lambda r: r.update_many({}, {"$set": {"name": "x"}}),
    "update_by_id": lambda r: r.update_by_id("a1", {"$set": {"name": "x"}}),
    "delete_one": lambda r: r.delete_one({"_id": "a1"}),
    "delete_by_id": lambda r: r.delete_by_id("a1"),
    "aggregate": lambda r: r.aggregate([]),
    "count": lambda r: r.count(),
}


def test_the_operation_list_matches_the_repository() -> None:
    public = {n for n in dir(TenantRepository) if not n.startswith("_") and callable(getattr(TenantRepository, n))}
    assert public == set(OPERATIONS)


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_every_operation_raises_without_a_context_and_changes_nothing(
    name: str, scratch: tuple[str, str], root_client: Client
) -> None:
    async def body(store: DataStore) -> None:
        with pytest.raises(NoTenantContextError):
            await OPERATIONS[name](store.tenant(COLLECTION))

    with_store(scratch, body)
    docs = list(root_client[scratch[1]][COLLECTION].find({}, sort=[("_id", 1)]))
    assert [d["_id"] for d in docs] == ["a1", "a2", "b1", "b2"]
    assert all(d["name"] != "x" for d in docs)


def test_reads_see_only_the_own_org(scratch: tuple[str, str]) -> None:
    async def body(store: DataStore) -> dict[str, Any]:
        repo = store.tenant(COLLECTION)
        seen: dict[str, Any] = {}
        for org in (ORG_A, ORG_B):
            with tenant_scope(org):
                seen[org] = {
                    "find": sorted(d["_id"] for d in await repo.find()),
                    "find_name": [d["_id"] for d in await repo.find({"name": {"$regex": "^B"}})],
                    "count": await repo.count(),
                    "find_one": await repo.find_one({"_id": "b1"}),
                    "agg": [d["_id"] for d in await repo.aggregate([{"$sort": {"rank": 1}}])],
                }
        return seen

    seen = with_store(scratch, body)
    assert seen[ORG_A] == {"find": ["a1", "a2"], "find_name": [], "count": 2, "find_one": None, "agg": ["a2", "a1"]}
    assert seen[ORG_B]["find"] == ["b1", "b2"]
    assert seen[ORG_B]["find_name"] == ["b1", "b2"]
    assert seen[ORG_B]["count"] == 2
    assert seen[ORG_B]["find_one"]["org_id"] == ORG_B
    assert seen[ORG_B]["agg"] == ["b2", "b1"]


def test_an_aggregate_starting_with_sort_and_group_sees_only_the_own_org(scratch: tuple[str, str]) -> None:
    async def body(store: DataStore) -> list[dict[str, Any]]:
        with tenant_scope(ORG_A):
            return await store.tenant(COLLECTION).aggregate(
                [{"$sort": {"rank": -1}}, {"$group": {"_id": "$org_id", "n": {"$sum": 1}}}]
            )

    assert with_store(scratch, body) == [{"_id": ORG_A, "n": 2}]


def test_a_foreign_id_is_not_found_for_get_update_and_delete(scratch: tuple[str, str], root_client: Client) -> None:
    async def body(store: DataStore) -> list[BaseException]:
        repo = store.tenant(COLLECTION)
        raised: list[BaseException] = []
        with tenant_scope(ORG_A):
            with pytest.raises(NotFound) as by_get:
                await repo.get("b1")
            with pytest.raises(NotFound) as by_update:
                await repo.update_by_id("b1", {"$set": {"name": "hijacked"}})
            with pytest.raises(NotFound) as by_delete:
                await repo.delete_by_id("b1")
            raised.extend([by_get.value, by_update.value, by_delete.value])
            own = await repo.get("a1")
        assert own["name"] == "Asha"
        return raised

    raised = with_store(scratch, body)
    assert len(raised) == 3
    assert not any(isinstance(err, PermissionError) for err in raised)
    assert root_client[scratch[1]][COLLECTION].find_one({"_id": "b1"}) == {
        "_id": "b1",
        "org_id": ORG_B,
        "name": "Bina",
        "rank": 3,
    }


def test_writes_land_in_the_own_org_only(scratch: tuple[str, str], root_client: Client) -> None:
    async def body(store: DataStore) -> None:
        repo = store.tenant(COLLECTION)
        with tenant_scope(ORG_A):
            await repo.insert_one({"_id": "a3", "name": "Anil"})
            await repo.insert_many([{"_id": "a4", "name": "Anu"}])
            await repo.update_many({}, {"$set": {"flag": True}})
            await repo.update_one({"_id": "b2"}, {"$set": {"name": "hijacked"}})
            assert await repo.delete_one({"_id": "b2"}) == 0
            await repo.delete_by_id("a4")

    with_store(scratch, body)
    coll = root_client[scratch[1]][COLLECTION]
    assert coll.find_one({"_id": "a3"}) == {"_id": "a3", "name": "Anil", "org_id": ORG_A, "flag": True}
    assert coll.find_one({"_id": "a4"}) is None
    assert coll.count_documents({"org_id": ORG_A, "flag": True}) == 3
    assert coll.count_documents({"org_id": ORG_B, "flag": {"$exists": True}}) == 0
    assert coll.find_one({"_id": "b2"}) == {"_id": "b2", "org_id": ORG_B, "name": "Bala", "rank": 0}


def test_an_upsert_creates_the_document_in_the_own_org(scratch: tuple[str, str], root_client: Client) -> None:
    async def body(store: DataStore) -> object:
        with tenant_scope(ORG_B):
            outcome = await store.tenant(COLLECTION).update_one(
                {"email_hash": "h_new"}, {"$set": {"name": "Bhanu"}, "$setOnInsert": {"rank": 9}}, upsert=True
            )
        return outcome.upserted_id

    upserted_id = with_store(scratch, body)
    created = root_client[scratch[1]][COLLECTION].find_one({"_id": upserted_id})
    assert created is not None
    assert (created["org_id"], created["email_hash"], created["name"], created["rank"]) == (ORG_B, "h_new", "Bhanu", 9)


def test_an_org_prefixed_unique_index_allows_the_same_value_in_two_orgs(scratch: tuple[str, str]) -> None:
    async def body(store: DataStore) -> None:
        # A fresh collection: the seeded hires have no email_hash, which a unique index would reject.
        await store.ensure_index("people", [("org_id", 1), ("email_hash", 1)], unique=True)
        repo = store.tenant("people")
        for org in (ORG_A, ORG_B):
            with tenant_scope(org):
                await repo.insert_one({"email_hash": "same"})
        with tenant_scope(ORG_A), pytest.raises(DuplicateKeyError):
            await repo.insert_one({"email_hash": "same"})

    with_store(scratch, body)


def test_ping_reaches_the_server(scratch: tuple[str, str]) -> None:
    async def body(store: DataStore) -> None:
        await store.ping()

    with_store(scratch, body)
