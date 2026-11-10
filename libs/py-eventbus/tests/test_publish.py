"""Publishing through the outbox (EVT-1 box 3; PRD §7.2).

A domain change, its event and one job per subscribed consumer are written in one transaction. A
repeated idempotency key in the same org is ignored; a rolled-back transaction leaves nothing.
The same behaviour against a real MongoDB replica set is in `tests_int/test_bus_against_mongo.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from onb_data import NoTenantContextError, TransactionError, tenant_scope
from onb_eventbus import JobStatus, PublishResult

from .helpers import CONSUMER, EVENT, ORG_A, ORG_B, make_bus


def _publish(bus: Any, org: str, key: str = "run-r1-started", **kwargs: Any) -> PublishResult:
    async def main() -> PublishResult:
        with tenant_scope(org):
            result: PublishResult = await bus.publish(EVENT, {"run_id": "r1"}, idempotency_key=key, **kwargs)
            return result

    return asyncio.run(main())


def test_publish_writes_the_event_and_one_job_per_consumer() -> None:
    bus, db, clock = make_bus(subscriptions={EVENT: [CONSUMER, "notify.welcome"]})
    result = _publish(bus, ORG_A, run_id="r1")
    assert not result.duplicate
    [event] = db["events"].docs
    assert event["_id"] == result.event_id
    assert {k: event[k] for k in ("org_id", "type", "payload", "run_id", "idempotency_key", "created_at")} == {
        "org_id": ORG_A,
        "type": EVENT,
        "payload": {"run_id": "r1"},
        "run_id": "r1",
        "idempotency_key": "run-r1-started",
        "created_at": clock.now,
    }
    jobs = db["jobs"].docs
    assert sorted(j["consumer"] for j in jobs) == sorted([CONSUMER, "notify.welcome"])
    assert sorted(j["_id"] for j in jobs) == sorted(result.job_ids)
    for job in jobs:
        assert job["org_id"] == ORG_A
        assert job["event_id"] == result.event_id
        assert job["event_type"] == EVENT
        assert job["status"] == JobStatus.PENDING
        assert job["attempts"] == 0
        assert job["max_attempts"] == 3
        assert job["available_at"] == clock.now
        assert job["payload"] == {"run_id": "r1"}


def test_ids_are_unguessable_uuids() -> None:
    bus, db, _ = make_bus()
    result = _publish(bus, ORG_A)
    assert len(result.event_id) == 36
    assert result.event_id.count("-") == 4
    assert result.event_id != db["jobs"].docs[0]["_id"]


def test_an_event_with_no_subscribers_writes_no_job() -> None:
    bus, db, _ = make_bus(subscriptions={})
    result = _publish(bus, ORG_A)
    assert result.job_ids == ()
    assert len(db["events"].docs) == 1
    assert db["jobs"].docs == []


def test_a_duplicate_idempotency_key_is_ignored() -> None:
    bus, db, _ = make_bus()
    first = _publish(bus, ORG_A)
    second = _publish(bus, ORG_A)
    assert second.duplicate
    assert second.event_id == first.event_id
    assert second.job_ids == ()
    assert len(db["events"].docs) == 1
    assert len(db["jobs"].docs) == 1


def test_a_duplicate_key_skips_the_domain_change_too() -> None:
    bus, db, _ = make_bus()
    calls: list[str] = []

    def change(tag: str) -> Any:
        async def apply() -> None:
            calls.append(tag)
            await bus.store.tenant("runs").insert_one({"tag": tag})

        return apply

    _publish(bus, ORG_A, change=change("first"))
    result = _publish(bus, ORG_A, change=change("second"))
    assert result.duplicate
    assert calls == ["first"]
    assert [d["tag"] for d in db["runs"].docs] == ["first"]


def test_the_same_key_in_another_org_is_a_different_event() -> None:
    bus, db, _ = make_bus()
    a = _publish(bus, ORG_A)
    b = _publish(bus, ORG_B)
    assert not b.duplicate
    assert a.event_id != b.event_id
    assert sorted(e["org_id"] for e in db["events"].docs) == [ORG_A, ORG_B]


def test_a_rolled_back_transaction_leaves_neither_change_nor_event() -> None:
    bus, db, _ = make_bus()
    store = bus.store

    async def change_then_fail() -> None:
        await store.tenant("runs").insert_one({"_id": "r1", "state": "started"})
        raise RuntimeError("domain rule broken")

    with pytest.raises(RuntimeError, match="domain rule"):
        _publish(bus, ORG_A, change=change_then_fail)
    assert db["runs"].docs == []
    assert db["events"].docs == []
    assert db["jobs"].docs == []


def test_a_failure_while_writing_the_jobs_rolls_back_the_change_and_the_event() -> None:
    bus, db, _ = make_bus()
    store = bus.store
    writes: list[str] = []

    async def fail_on_third_write() -> None:
        writes.append("w")
        if len(writes) == 3:
            raise ConnectionError("primary stepped down")

    async def change() -> None:
        await store.tenant("runs").insert_one({"_id": "r1"})

    db.write_hook = fail_on_third_write
    with pytest.raises(ConnectionError):
        _publish(bus, ORG_A, change=change)
    db.write_hook = None
    assert (db["runs"].docs, db["events"].docs, db["jobs"].docs) == ([], [], [])


def test_publish_takes_the_org_from_the_context_only() -> None:
    bus, _, _ = make_bus()

    async def main() -> None:
        await bus.publish(EVENT, {}, idempotency_key="k")

    with pytest.raises(NoTenantContextError):
        asyncio.run(main())


def test_publish_does_not_nest_in_another_transaction() -> None:
    bus, _, _ = make_bus()

    async def main() -> None:
        with tenant_scope(ORG_A):

            async def inner() -> None:
                await bus.publish(EVENT, {}, idempotency_key="k")

            await bus.store.run_transaction(inner)

    with pytest.raises(TransactionError):
        asyncio.run(main())


@pytest.mark.parametrize(
    ("event_type", "payload", "key", "run_id"),
    [
        ("", {}, "k", None),
        ("bad type", {}, "k", None),
        (EVENT, {}, "", None),
        (EVENT, {}, "x" * 201, None),
        (EVENT, {}, 7, None),
        (EVENT, [], "k", None),
        (EVENT, {}, "k", ""),
    ],
    ids=str,
)
def test_bad_publish_arguments_are_refused_before_any_write(
    event_type: Any, payload: Any, key: Any, run_id: Any
) -> None:
    bus, db, _ = make_bus()

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(event_type, payload, idempotency_key=key, run_id=run_id)

    with pytest.raises((ValueError, TypeError)):
        asyncio.run(main())
    assert db["events"].docs == []


def test_the_payload_is_copied_not_shared_with_the_caller() -> None:
    bus, db, _ = make_bus()
    payload: dict[str, Any] = {"tasks": ["a"]}

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(EVENT, payload, idempotency_key="k")

    asyncio.run(main())
    payload["tasks"].append("b")
    assert db["events"].docs[0]["payload"] == {"tasks": ["a"]}
