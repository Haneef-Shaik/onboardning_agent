"""The `notify.in_app` consumer: change-request outcomes and blocked runs delivered in-app, idempotently."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_mcp_mocks.notify.in_app import MalformedEventError, ensure_in_app_indexes, in_app_handler

ORG_A, ORG_B = "org_acme", "org_globex"


@pytest.fixture
def db() -> MemoryDatabase:
    database = MemoryDatabase()
    store = memory_store(database)

    async def seed() -> None:
        await ensure_in_app_indexes(store)
        with tenant_scope(ORG_A):
            await store.tenant("users").insert_many(
                [
                    {"_id": "u_acme_hradmin", "role": "HR_ADMIN"},
                    {"_id": "u_acme_hr1", "role": "HR"},
                    {"_id": "u_acme_mgr_ravi", "role": "MANAGER"},
                ]
            )
        with tenant_scope(ORG_B):
            await store.tenant("users").insert_one({"_id": "u_globex_hr1", "role": "HR"})

    asyncio.run(seed())
    return database


def run(
    db: MemoryDatabase, event_type: str, payload: Mapping[str, Any], *, org: str = ORG_A, event_id: str = "e1"
) -> None:
    job = JobContext(
        job_id=f"j-{event_id}",
        org_id=org,
        consumer="notify.in_app",
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        run_id=None,
        attempt=1,
    )

    async def go() -> None:
        with tenant_scope(org):
            await in_app_handler(memory_store(db))(job)

    asyncio.run(go())


def stored(db: MemoryDatabase) -> list[tuple[str, str, str]]:
    return sorted((n["org_id"], n["user_id"], n["kind"]) for n in db.raw("notifications"))


def test_a_decided_change_request_tells_its_requester() -> None:
    db = MemoryDatabase()
    store = memory_store(db)

    async def seed() -> None:
        with tenant_scope(ORG_A):
            await store.tenant("users").insert_one({"_id": "u_acme_mgr_ravi", "role": "MANAGER"})

    asyncio.run(seed())
    run(
        db,
        "change_request.decided",
        {"change_request_id": "cr_1", "requested_by": "u_acme_mgr_ravi", "decision": "accepted"},
    )
    [note] = db.raw("notifications")
    assert (note["user_id"], note["kind"], note["ref"]) == (
        "u_acme_mgr_ravi",
        "change_request.decided",
        {"type": "change_request", "id": "cr_1"},
    )
    assert note["body"] == "Your template change request was accepted."


def test_a_blocked_run_tells_the_orgs_hr_and_hr_admin_users_only(db: MemoryDatabase) -> None:
    run(db, "run.blocked", {"run_id": "run_acme_001", "task_id": "it_laptop"})
    assert stored(db) == [(ORG_A, "u_acme_hr1", "run.blocked"), (ORG_A, "u_acme_hradmin", "run.blocked")]


def test_a_redelivered_job_stores_nothing_new(db: MemoryDatabase) -> None:
    payload = {"run_id": "run_acme_001", "task_id": "it_laptop"}
    run(db, "run.blocked", payload)
    run(db, "run.blocked", payload)  # at-least-once: the same event again
    assert len(db.raw("notifications")) == 2
    run(db, "run.blocked", payload, event_id="e2")  # a new event is new news
    assert len(db.raw("notifications")) == 4


def test_an_org_a_event_never_notifies_org_b(db: MemoryDatabase) -> None:
    run(
        db,
        "change_request.decided",
        {"change_request_id": "cr_1", "requested_by": "u_globex_hr1", "decision": "rejected"},
    )
    run(db, "run.blocked", {"run_id": "run_acme_001", "task_id": "it_laptop"})
    assert all(org == ORG_A for org, _, _ in stored(db))
    assert "u_globex_hr1" not in {user for _, user, _ in stored(db)}


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("change_request.decided", {"change_request_id": "cr_1", "requested_by": "u 1", "decision": "accepted"}),
        ("change_request.decided", {"change_request_id": "cr_1", "requested_by": "u_1", "decision": "maybe"}),
        ("run.blocked", {"run_id": {"$ne": None}, "task_id": "t"}),
        ("run.blocked", {"task_id": "t"}),
        ("something.else", {}),
    ],
    ids=["bad-user", "bad-decision", "operator-run-id", "no-run-id", "unsubscribed-type"],
)
def test_a_malformed_event_is_refused_so_the_bus_retries_then_dead_letters(
    db: MemoryDatabase, event_type: str, payload: Mapping[str, Any]
) -> None:
    with pytest.raises(MalformedEventError):
        run(db, event_type, payload)
    assert db.raw("notifications") == []
