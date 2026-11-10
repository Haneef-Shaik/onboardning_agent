"""The safety net allows the worker queue's real cross-org commands, and nothing else (T-2 with EVT-1).

Runs the same `QueueRepository` calls the event bus worker makes (ADR 0002): claim, heartbeat,
complete, retry, and the dead-letter move of a job without `org_id`, in a transaction. The net must
stay silent for those. Any other unscoped command on `jobs`/`dead_letters` must still be flagged.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data import DataStore, connect
from onb_data.safety_net import TenantCommandListener
from pymongo import MongoClient

Client = MongoClient[dict[str, object]]
NOW = datetime(2026, 11, 10, tzinfo=UTC)
CONSUMER = "orchestrator.start_run"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    """(uri, database) with one due job in org_alpha and one orphan job (no org_id)."""
    name = f"it_t2_queue_{uuid.uuid4().hex[:12]}"
    base = {"consumer": CONSUMER, "status": "pending", "available_at": NOW, "attempts": 0, "max_attempts": 3}
    root_client[name]["jobs"].insert_one({"_id": "job-a", "org_id": "org_alpha", **base})
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    yield f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true", name
    root_client.drop_database(name)


def with_store[T](scratch: tuple[str, str], body: Callable[[DataStore], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        store = connect(scratch[0], scratch[1])
        try:
            return await body(store)
        finally:
            await store.close()

    return asyncio.run(main())


def claim_query() -> dict[str, Any]:
    """The shape of `onb_eventbus.documents.claim_query`."""
    return {
        "consumer": {"$in": [CONSUMER]},
        "attempts": {"$type": "number"},
        "max_attempts": {"$type": "number"},
        "$or": [
            {"status": "pending", "available_at": {"$lte": NOW + timedelta(days=1)}},
            {"status": "running", "locked_until": {"$lte": NOW + timedelta(days=1)}},
        ],
    }


def claim_update(token: str) -> dict[str, Any]:
    return {
        "$set": {"status": "running", "lease_token": token, "lease_owner": "w1", "locked_until": NOW},
        "$inc": {"attempts": 1},
    }


def test_the_worker_queue_cycle_passes_the_net(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: tuple[str, str]
) -> None:
    async def cycle(store: DataStore) -> tuple[Any, ...]:
        jobs = store.queue("jobs")
        claimed = await jobs.claim(claim_query(), claim_update("t1"), sort=[("available_at", 1)])
        beat = await jobs.update_leased("job-a", "t1", {"$set": {"locked_until": NOW, "updated_at": NOW}})
        retried = await jobs.update_leased(
            "job-a",
            "t1",
            {
                "$set": {"status": "pending", "available_at": NOW, "last_error": "RuntimeError"},
                "$unset": {"lease_token": ""},
            },
        )
        reclaimed = await jobs.claim(claim_query(), claim_update("t2"))
        done = await jobs.update_leased(
            "job-a", "t2", {"$set": {"status": "done", "completed_at": NOW}, "$unset": {"lease_token": ""}}
        )
        return claimed, beat, retried, reclaimed, done

    claimed, beat, retried, reclaimed, done = with_store(scratch, cycle)
    assert claimed is not None
    assert claimed["org_id"] == "org_alpha"
    assert reclaimed is not None
    assert reclaimed["attempts"] == 2
    assert (beat, retried, done) == (True, True, True)
    assert tenant_safety_net.violations == ()


def test_the_orphan_dead_letter_move_passes_the_net(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: tuple[str, str]
) -> None:
    """A job without `org_id` is claimed, then moved to `dead_letters` in one transaction, as given."""
    root_client[scratch[1]]["jobs"].update_one({"_id": "job-a", "org_id": "org_alpha"}, {"$set": {"status": "done"}})
    with tenant_safety_net.allowing_unscoped("test setup: plant a job that has no org_id"):
        root_client[scratch[1]]["jobs"].insert_one(
            {
                "_id": "orphan",
                "consumer": CONSUMER,
                "status": "pending",
                "available_at": NOW,
                "attempts": 0,
                "max_attempts": 3,
            }
        )

    async def move(store: DataStore) -> Any:
        job = await store.queue("jobs").claim(claim_query(), claim_update("t9"))
        assert job is not None
        letter = {key: value for key, value in job.items() if key not in {"lease_token", "lease_owner", "locked_until"}}

        async def in_one_transaction() -> None:
            await store.queue("dead_letters").insert_one({**letter, "status": "dead", "reason": "missing_org_id"})
            assert await store.queue("jobs").delete_leased(job["_id"], "t9")

        await store.run_transaction(in_one_transaction)
        return job["_id"]

    assert with_store(scratch, move) == "orphan"
    assert tenant_safety_net.violations == ()
    with tenant_safety_net.allowing_unscoped("root read-back of the orphan, which has no org to scope by"):
        letter = root_client[scratch[1]]["dead_letters"].find_one({"_id": "orphan"})
    assert letter is not None
    assert (letter["reason"], "org_id" in letter) == ("missing_org_id", False)


OTHER_OPS: dict[str, tuple[str, Callable[[Any], object]]] = {
    "find jobs by status": ("find", lambda db: db.jobs.find_one({"status": "pending"})),
    "update many jobs": ("update", lambda db: db.jobs.update_many({}, {"$set": {"status": "pending"}})),
    "update a job by id only": ("update", lambda db: db.jobs.update_one({"_id": "job-a"}, {"$set": {"status": "x"}})),
    "move a job to another org": (
        "update",
        lambda db: db.jobs.update_one({"_id": "job-a", "lease_token": "t"}, {"$set": {"org_id": "org_beta"}}),
    ),
    "delete all dead letters": ("delete", lambda db: db.dead_letters.delete_many({})),
    "count jobs": ("aggregate", lambda db: db.jobs.count_documents({})),
    "claim a dead letter": (
        "findAndModify",
        lambda db: db.dead_letters.find_one_and_update({"status": "dead"}, {"$set": {"status": "running"}}),
    ),
    "insert a job without org": ("insert", lambda db: db.jobs.insert_one({"consumer": CONSUMER})),
    "orphan dead letter for another reason": (
        "insert",
        lambda db: db.dead_letters.insert_one({"status": "dead", "reason": "exhausted"}),
    ),
}


@pytest.mark.parametrize("name", sorted(OTHER_OPS))
def test_any_other_unscoped_queue_command_is_still_flagged(
    name: str, tenant_safety_net: TenantCommandListener, root_client: Client, scratch: tuple[str, str]
) -> None:
    command, operation = OTHER_OPS[name]
    raw: Client = MongoClient(scratch[0], serverSelectionTimeoutMS=10_000)
    try:
        operation(raw[scratch[1]])
    finally:
        raw.close()
    drained = tenant_safety_net.drain()
    assert [v.command for v in drained] == [command]


def test_the_operator_dead_letter_read_is_flagged(
    tenant_safety_net: TenantCommandListener, scratch: tuple[str, str]
) -> None:
    """`QueueRepository.find` on dead_letters reads across orgs; it is not a worker command (ADR 0002)."""

    async def read(store: DataStore) -> list[dict[str, Any]]:
        return await store.queue("dead_letters").find({}, limit=10)

    with_store(scratch, read)
    assert [(v.collection, v.command) for v in tenant_safety_net.drain()] == [("dead_letters", "find")]
