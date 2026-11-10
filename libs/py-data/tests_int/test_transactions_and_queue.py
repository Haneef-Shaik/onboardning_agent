"""`DataStore.run_transaction` and the queue repository against the compose replica set (EVT-1).

Read back with the root client, so the store is not trusted to report on itself.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from urllib.parse import quote_plus

import pytest
from onb_data import connect, tenant_scope
from pymongo import MongoClient
from pymongo.database import Database

Client = MongoClient[dict[str, object]]
ORG = "org_alpha"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, Database[dict[str, object]]]]:
    name = f"it_evt1_data_{uuid.uuid4().hex[:12]}"
    db = root_client[name]
    for collection in ("runs", "events", "jobs", "dead_letters"):
        db.create_collection(collection)
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    uri = f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"
    try:
        yield uri, db
    finally:
        root_client.drop_database(name)


def test_a_transaction_commits_every_repository_write_together(
    scratch: tuple[str, Database[dict[str, object]]],
) -> None:
    uri, db = scratch

    async def main() -> None:
        store = connect(uri, db.name)
        try:
            with tenant_scope(ORG):

                async def both() -> None:
                    await store.tenant("runs").insert_one({"_id": "r1"})
                    await store.tenant("events").insert_one({"_id": "e1", "run_id": "r1"})

                await store.run_transaction(both)
        finally:
            await store.close()

    asyncio.run(main())
    assert db.runs.find_one({"_id": "r1"}) == {"_id": "r1", "org_id": ORG}
    assert db.events.count_documents({"run_id": "r1", "org_id": ORG}) == 1


def test_a_raising_callback_rolls_back_every_repository_write(
    scratch: tuple[str, Database[dict[str, object]]],
) -> None:
    uri, db = scratch

    async def main() -> None:
        store = connect(uri, db.name)
        try:
            with tenant_scope(ORG):

                async def write_then_fail() -> None:
                    await store.tenant("runs").insert_one({"_id": "r1"})
                    await store.queue("jobs").insert_one({"_id": "j1", "org_id": ORG})
                    raise RuntimeError("rollback")

                with pytest.raises(RuntimeError, match="rollback"):
                    await store.run_transaction(write_then_fail)
        finally:
            await store.close()

    asyncio.run(main())
    assert db.runs.count_documents({}) == 0
    assert db.jobs.count_documents({}) == 0


def test_concurrent_claims_on_one_document_succeed_once(scratch: tuple[str, Database[dict[str, object]]]) -> None:
    uri, db = scratch
    db.jobs.insert_one({"_id": "j1", "org_id": ORG, "status": "pending"})

    async def claim(token: str) -> object:
        store = connect(uri, db.name)
        try:
            return await store.queue("jobs").claim(
                {"status": "pending"}, {"$set": {"status": "running", "lease_token": token}}
            )
        finally:
            await store.close()

    async def main() -> list[object]:
        return list(await asyncio.gather(*(claim(f"t{i}") for i in range(10))))

    results = [r for r in asyncio.run(main()) if r is not None]
    assert len(results) == 1
    [winner] = results
    assert isinstance(winner, dict)
    assert db.jobs.find_one({"_id": "j1"}) == {
        "_id": "j1",
        "org_id": ORG,
        "status": "running",
        "lease_token": winner["lease_token"],
    }


def test_a_leased_write_with_a_stale_token_changes_nothing(scratch: tuple[str, Database[dict[str, object]]]) -> None:
    uri, db = scratch
    db.jobs.insert_one({"_id": "j1", "org_id": ORG, "status": "running", "lease_token": "current"})

    async def main() -> tuple[bool, bool]:
        store = connect(uri, db.name)
        try:
            queue = store.queue("jobs")
            updated = await queue.update_leased("j1", "stale", {"$set": {"status": "done"}})
            deleted = await queue.delete_leased("j1", "stale")
            return updated, deleted
        finally:
            await store.close()

    assert asyncio.run(main()) == (False, False)
    assert db.jobs.find_one({"_id": "j1"}) == {
        "_id": "j1",
        "org_id": ORG,
        "status": "running",
        "lease_token": "current",
    }
