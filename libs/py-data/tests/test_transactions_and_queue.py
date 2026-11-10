"""Transactions and the job-queue repository (EVT-1; PRD §7.2 outbox, §7.9 Event bus).

`DataStore.run_transaction` runs a callback in one MongoDB transaction: every repository call made
inside it carries the session. `DataStore.queue` hands out the narrow cross-org view of `jobs` and
`dead_letters` that workers need to claim jobs before they know the job's org. Real transaction and
claim semantics are covered in `tests_int/test_transactions_and_queue.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from onb_data import (
    GLOBAL_COLLECTIONS,
    QUEUE_COLLECTIONS,
    InvalidIndexError,
    QueueRepository,
    TenantViolationError,
    TransactionError,
    UnknownCollectionError,
    in_transaction,
    tenant_scope,
)
from pymongo import ReturnDocument

from .fakes import FakeClient, FakeCollection, FakeDatabase, make_store

ORG = "org_a"


def test_the_queue_collections_are_jobs_and_dead_letters() -> None:
    assert frozenset({"jobs", "dead_letters"}) == QUEUE_COLLECTIONS
    assert not QUEUE_COLLECTIONS & GLOBAL_COLLECTIONS


@pytest.mark.parametrize("name", sorted(QUEUE_COLLECTIONS))
def test_queue_collections_get_a_queue_repository(name: str) -> None:
    repo = make_store().queue(name)
    assert isinstance(repo, QueueRepository)
    assert repo.collection_name == name


@pytest.mark.parametrize("name", ["hires", "events", "orgs"])
def test_only_queue_collections_get_a_queue_repository(name: str) -> None:
    with pytest.raises(UnknownCollectionError, match=name):
        make_store().queue(name)


def test_producers_still_write_jobs_through_a_tenant_repository() -> None:
    db = FakeDatabase()

    async def main() -> None:
        with tenant_scope(ORG):
            await make_store(db).tenant("jobs").insert_one({"consumer": "c"})

    asyncio.run(main())
    assert db["jobs"].last("insert_one").args[0] == {"consumer": "c", "org_id": ORG}


# --- the queue repository ------------------------------------------------------------------


def test_claim_is_one_atomic_find_one_and_update_returning_the_claimed_document() -> None:
    jobs = FakeCollection(name="jobs", find_one_and_update_result={"_id": "j1", "status": "running"})
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    found = asyncio.run(repo.claim({"status": "pending"}, {"$set": {"status": "running"}}, sort=[("available_at", 1)]))
    assert found == {"_id": "j1", "status": "running"}
    call = jobs.last("find_one_and_update")
    assert call.args == ({"status": "pending"}, {"$set": {"status": "running"}})
    assert call.kwargs == {"sort": [("available_at", 1)], "return_document": ReturnDocument.AFTER}


def test_claim_needs_no_tenant_context_because_the_org_is_unknown_until_claimed() -> None:
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    assert asyncio.run(repo.claim({}, {"$set": {"status": "running"}})) is None
    assert "org_id" not in jobs.last("find_one_and_update").args[0]


def test_claim_refuses_a_replacement_document() -> None:
    repo = make_store().queue("jobs")
    with pytest.raises(ValueError, match="operators"):
        asyncio.run(repo.claim({}, {"status": "running"}))


@pytest.mark.parametrize("update", [{"$set": {"org_id": "org_b"}}, {"$unset": {"org_id": ""}}])
def test_a_worker_can_never_move_a_job_to_another_org(update: dict[str, Any]) -> None:
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    with pytest.raises(TenantViolationError):
        asyncio.run(repo.claim({}, update))
    with pytest.raises(TenantViolationError):
        asyncio.run(repo.update_leased("j1", "tok", update))
    assert jobs.calls == []


@pytest.mark.parametrize(
    "query",
    [
        {"org_id": "org_b"},
        {"payload.hire_id": "h1"},
        {"_id": "j1"},
        {"$or": [{"status": "pending"}, {"org_id": "org_b"}]},
        {"$where": "true"},
        {"status": {"$in": ["pending"]}, "$and": [{"run_id": "r1"}]},
    ],
    ids=str,
)
def test_claim_filters_only_on_queue_state_never_on_an_org_or_job_content(query: dict[str, Any]) -> None:
    """A claim cannot be aimed at another org's jobs or used to search job contents (security review)."""
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    with pytest.raises(TenantViolationError, match="claim"):
        asyncio.run(repo.claim(query, {"$set": {"status": "running"}}))
    assert jobs.calls == []


@pytest.mark.parametrize(
    "update",
    [
        {"$set": {"payload": {"x": 1}}},
        {"$set": {"consumer": "other.consumer"}},
        {"$set": {"payload.hire_id": "h2"}},
        {"$inc": {"max_attempts": 10}},
        {"$push": {"history": 1}},
        {"$rename": {"status": "payload"}},
    ],
    ids=str,
)
def test_worker_writes_touch_only_queue_state_never_job_content(update: dict[str, Any]) -> None:
    """A worker may move a job through its states; it may not rewrite what the job does."""
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    with pytest.raises(TenantViolationError, match="queue state"):
        asyncio.run(repo.claim({}, update))
    with pytest.raises(TenantViolationError, match="queue state"):
        asyncio.run(repo.update_leased("j1", "tok", update))
    assert jobs.calls == []


def test_the_worker_state_writes_the_bus_needs_are_accepted() -> None:
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    claim_query = {
        "consumer": {"$in": ["c"]},
        "attempts": {"$type": "number"},
        "$or": [{"status": "pending", "available_at": {"$lte": 1}}, {"status": "running", "locked_until": {"$lte": 1}}],
    }
    update = {
        "$set": {"status": "running", "lease_token": "t", "lease_owner": "w", "locked_until": 2},
        "$inc": {"attempts": 1},
    }
    asyncio.run(repo.claim(claim_query, update))
    asyncio.run(repo.update_leased("j1", "t", {"$set": {"status": "done"}, "$unset": {"lease_token": ""}}))
    assert [call.method for call in jobs.calls] == ["find_one_and_update", "update_one"]


@pytest.mark.parametrize("operation", ["insert_one", "find"])
def test_jobs_cannot_be_written_or_searched_through_the_queue(operation: str) -> None:
    """Jobs are written only by producers through `tenant("jobs")`, which sets `org_id` (security review)."""
    jobs = FakeCollection(name="jobs")
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    attempt = (
        repo.insert_one({"_id": "forged", "org_id": "org_b", "consumer": "c"})
        if operation == "insert_one"
        else repo.find({"org_id": "org_b"}, limit=10)
    )
    with pytest.raises(TenantViolationError, match="dead_letters"):
        asyncio.run(attempt)
    assert jobs.calls == []


def test_leased_updates_and_deletes_match_the_id_and_the_lease_token() -> None:
    jobs = FakeCollection(name="jobs", matched_count=1, deleted_count=0)
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    assert asyncio.run(repo.update_leased("j1", "tok", {"$set": {"status": "done"}})) is True
    assert jobs.last("update_one").args == ({"_id": "j1", "lease_token": "tok"}, {"$set": {"status": "done"}})
    assert asyncio.run(repo.delete_leased("j1", "tok")) is False
    assert jobs.last("delete_one").args == ({"_id": "j1", "lease_token": "tok"},)


@pytest.mark.parametrize("token", ["", None, {"$ne": "x"}, 3])
def test_a_lease_token_must_be_a_non_empty_string(token: Any) -> None:
    repo = make_store().queue("jobs")
    with pytest.raises(TypeError, match="lease token"):
        asyncio.run(repo.delete_leased("j1", token))
    with pytest.raises(TypeError, match="lease token"):
        asyncio.run(repo.update_leased("j1", token, {"$set": {"a": 1}}))


@pytest.mark.parametrize("job_id", [{"$ne": "x"}, ["j1"], ("j1",)])
def test_a_leased_write_names_exactly_one_job(job_id: Any) -> None:
    repo = make_store().queue("jobs")
    with pytest.raises(TypeError, match="job id"):
        asyncio.run(repo.delete_leased(job_id, "tok"))


def test_a_lost_lease_reports_false() -> None:
    jobs = FakeCollection(name="jobs", matched_count=0)
    repo = make_store(FakeDatabase({"jobs": jobs})).queue("jobs")
    assert asyncio.run(repo.update_leased("j1", "tok", {"$set": {"status": "done"}})) is False


def test_a_dead_letter_keeps_the_document_as_given_without_adding_an_org() -> None:
    dead = FakeCollection(name="dead_letters")
    repo = make_store(FakeDatabase({"dead_letters": dead})).queue("dead_letters")
    assert asyncio.run(repo.insert_one({"_id": "j1", "reason": "missing org_id"})) == "j1"
    assert dead.last("insert_one").args[0] == {"_id": "j1", "reason": "missing org_id"}


def test_find_reads_queue_documents_with_a_bounded_limit() -> None:
    dead = FakeCollection(name="dead_letters", docs=[{"_id": "j1"}])
    repo = make_store(FakeDatabase({"dead_letters": dead})).queue("dead_letters")
    assert asyncio.run(repo.find({"reason": "x"}, limit=10)) == [{"_id": "j1"}]
    call = dead.last("find")
    assert call.args == ({"reason": "x"},)
    assert call.kwargs == {"limit": 10}
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(repo.find({}, limit=0))


# --- indexes on queue collections -----------------------------------------------------------


def test_a_queue_claim_index_may_start_with_status() -> None:
    db = FakeDatabase()
    asyncio.run(make_store(db).ensure_index("jobs", [("status", 1), ("available_at", 1)]))
    assert db["jobs"].last("create_index").args == ([("status", 1), ("available_at", 1)],)


def test_a_unique_queue_index_must_still_start_with_org_id() -> None:
    db = FakeDatabase()
    with pytest.raises(InvalidIndexError, match="org_id"):
        asyncio.run(make_store(db).ensure_index("jobs", [("event_id", 1)], unique=True))
    assert db["jobs"].calls == []


# --- transactions --------------------------------------------------------------------------


def test_every_call_inside_a_transaction_carries_the_session() -> None:
    db, client = FakeDatabase(), FakeClient()
    store = make_store(db, client)

    async def body() -> str:
        assert in_transaction()
        with tenant_scope(ORG):
            await store.tenant("runs").insert_one({"_id": "r1"})
            await store.tenant("events").find_one({"type": "x"})
        await store.queue("jobs").delete_leased("j1", "tok")
        return "ok"

    assert asyncio.run(store.run_transaction(body)) == "ok"
    session = client.sessions[0]
    assert session.transactions == 1
    assert session.ended
    assert db["runs"].last("insert_one").kwargs == {"session": session}
    assert db["events"].last("find_one").kwargs["session"] is session
    assert db["jobs"].last("delete_one").kwargs == {"session": session}


def test_calls_outside_a_transaction_carry_no_session() -> None:
    db, client = FakeDatabase(), FakeClient()
    store = make_store(db, client)

    async def main() -> None:
        await store.run_transaction(_noop)
        assert not in_transaction()
        with tenant_scope(ORG):
            await store.tenant("runs").insert_one({"_id": "r1"})

    asyncio.run(main())
    assert db["runs"].last("insert_one").kwargs == {}


def test_an_error_in_the_callback_propagates_and_clears_the_session() -> None:
    client = FakeClient()
    store = make_store(FakeDatabase(), client)

    async def failing() -> None:
        raise RuntimeError("boom")

    async def main() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            await store.run_transaction(failing)
        assert not in_transaction()

    asyncio.run(main())
    assert client.sessions[0].aborted == 1


def test_a_task_started_inside_a_transaction_cannot_use_its_session() -> None:
    """A session is not safe for concurrent use; a child task gets an error, not a shared session."""
    db, client = FakeDatabase(), FakeClient()
    store = make_store(db, client)
    seen: list[bool] = []

    async def body() -> None:
        async def child() -> None:
            seen.append(in_transaction())
            with tenant_scope(ORG):
                await store.tenant("runs").insert_one({"_id": "r1"})

        with pytest.raises(TransactionError, match="task"):
            await asyncio.create_task(child())

    asyncio.run(store.run_transaction(body))
    assert seen == [False]
    assert db["runs"].calls == []


def test_transactions_do_not_nest() -> None:
    store = make_store(FakeDatabase(), FakeClient())

    async def outer() -> None:
        await store.run_transaction(_noop)

    with pytest.raises(TransactionError, match="nest"):
        asyncio.run(store.run_transaction(outer))


def test_a_store_without_a_client_cannot_run_a_transaction() -> None:
    with pytest.raises(TransactionError, match="client"):
        asyncio.run(make_store().run_transaction(_noop))


async def _noop() -> None:
    return None
