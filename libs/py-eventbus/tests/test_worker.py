"""Workers: claim by lease, heartbeat, retry with backoff, dead letters (EVT-1 boxes 1, 2, 5; PRD §7.2).

The clock is moved by hand, so lease expiry needs no sleeping. The same behaviour against a real
MongoDB, with workers on separate clients and threads, is in `tests_int/test_bus_against_mongo.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from onb_data import NoTenantContextError, current_tenant, tenant_scope
from onb_eventbus import DeadLetterReason, JobContext, JobStatus, WorkOutcome

from .helpers import CONSUMER, EVENT, FAST, ORG_A, ORG_B, Clock, make_bus
from .memory_mongo import MemoryDatabase


async def _publish(bus: Any, org: str = ORG_A, key: str = "k1", run_id: str | None = "r1") -> str:
    with tenant_scope(org):
        result = await bus.publish(EVENT, {"run_id": run_id}, idempotency_key=key, run_id=run_id)
    job_id: str = result.job_ids[0]
    return job_id


async def _ok(job: JobContext) -> None:
    return None


async def _fail(job: JobContext) -> None:
    raise RuntimeError("downstream said no for alice@example.com")


def _job(db: MemoryDatabase, job_id: str) -> dict[str, Any]:
    [job] = [d for d in db["jobs"].docs if d["_id"] == job_id]
    return dict(job)


# --- the happy path ------------------------------------------------------------------------


def test_no_claimable_job_means_no_work() -> None:
    bus, _, _ = make_bus()
    assert asyncio.run(bus.worker({CONSUMER: _ok}).run_once()) is None


def test_a_handler_gets_the_job_with_the_tenant_context_set_from_the_job() -> None:
    bus, db, _ = make_bus()
    seen: list[tuple[JobContext, str]] = []

    async def handler(job: JobContext) -> None:
        seen.append((job, current_tenant().org_id))

    async def main() -> Any:
        job_id = await _publish(bus)
        return job_id, await bus.worker({CONSUMER: handler}, worker_id="w1").run_once()

    job_id, processed = asyncio.run(main())
    assert processed.job_id == job_id
    assert processed.outcome is WorkOutcome.COMPLETED
    [(job, org_in_handler)] = seen
    assert org_in_handler == ORG_A
    assert (job.org_id, job.consumer, job.event_type, job.run_id, job.attempt) == (ORG_A, CONSUMER, EVENT, "r1", 1)
    assert job.payload == {"run_id": "r1"}
    assert job.event_id == db["events"].docs[0]["_id"]
    done = _job(db, job_id)
    assert done["status"] == JobStatus.DONE
    assert "lease_token" not in done
    assert "locked_until" not in done


def test_the_handler_payload_is_read_only() -> None:
    bus, _, _ = make_bus()
    errors: list[BaseException] = []

    async def handler(job: JobContext) -> None:
        try:
            job.payload["run_id"] = "other"  # type: ignore[index]
        except TypeError as exc:
            errors.append(exc)

    async def main() -> None:
        await _publish(bus)
        await bus.worker({CONSUMER: handler}).run_once()

    asyncio.run(main())
    assert len(errors) == 1


def test_a_consumer_reads_only_its_jobs_org() -> None:
    """The consumer half of the cross-tenant rule (PRD §7.9): a job for org A sees org A data only."""
    bus, _, _ = make_bus()
    store = bus.store
    seen: list[list[str]] = []

    async def handler(job: JobContext) -> None:
        seen.append([doc["_id"] for doc in await store.tenant("hires").find()])

    async def main() -> None:
        with tenant_scope(ORG_A):
            await store.tenant("hires").insert_one({"_id": "a1"})
        with tenant_scope(ORG_B):
            await store.tenant("hires").insert_one({"_id": "b1"})
        await _publish(bus, ORG_A, key="ka")
        await _publish(bus, ORG_B, key="kb")
        worker = bus.worker({CONSUMER: handler})
        await worker.run_once()
        await worker.run_once()

    asyncio.run(main())
    assert sorted(seen) == [["a1"], ["b1"]]


def test_the_worker_leaves_no_tenant_context_behind() -> None:
    bus, _, _ = make_bus()

    async def main() -> None:
        await _publish(bus)
        await bus.worker({CONSUMER: _ok}).run_once()
        with pytest.raises(NoTenantContextError):
            current_tenant()

    asyncio.run(main())


# --- box 1: two workers never hold the same job ----------------------------------------------


def test_a_claimed_job_is_invisible_to_a_second_worker_until_its_lease_expires() -> None:
    bus, _, _ = make_bus()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(job: JobContext) -> None:
        started.set()
        await release.wait()

    async def main() -> tuple[Any, Any]:
        await _publish(bus)
        first = asyncio.create_task(bus.worker({CONSUMER: slow}, worker_id="w1").run_once())
        await started.wait()
        second = await bus.worker({CONSUMER: _ok}, worker_id="w2").run_once()
        release.set()
        return second, await first

    second, first = asyncio.run(main())
    assert second is None
    assert first.outcome is WorkOutcome.COMPLETED


def test_concurrent_workers_process_each_job_exactly_once_and_never_together() -> None:
    bus, db, _ = make_bus()
    active: set[str] = set()
    overlaps: list[str] = []
    runs: dict[str, int] = {}

    async def handler(job: JobContext) -> None:
        if job.job_id in active:
            overlaps.append(job.job_id)
        active.add(job.job_id)
        runs[job.job_id] = runs.get(job.job_id, 0) + 1
        for _ in range(3):
            await asyncio.sleep(0)
        active.discard(job.job_id)

    async def drain(worker_id: str) -> None:
        worker = bus.worker({CONSUMER: handler}, worker_id=worker_id)
        while await worker.run_once() is not None:
            pass

    async def main() -> None:
        for i in range(20):
            await _publish(bus, key=f"k{i}")
        await asyncio.gather(*(drain(f"w{i}") for i in range(4)))

    asyncio.run(main())
    assert overlaps == []
    assert len(runs) == 20
    assert set(runs.values()) == {1}
    assert {d["status"] for d in db["jobs"].docs} == {JobStatus.DONE}


# --- box 2: retries, crashes and dead letters ------------------------------------------------


def test_a_failed_job_is_retried_after_exponential_backoff_and_keeps_no_error_text() -> None:
    bus, db, clock = make_bus()

    published_at = clock.now

    async def main() -> tuple[Any, ...]:
        job_id = await _publish(bus)
        first = await bus.worker({CONSUMER: _fail}).run_once()
        snapshot = dict(_job(db, job_id))
        too_early = await bus.worker({CONSUMER: _ok}).run_once()
        clock.advance(FAST.backoff_base_seconds)
        second = await bus.worker({CONSUMER: _fail}).run_once()
        after_second = dict(_job(db, job_id))
        return job_id, first, snapshot, too_early, second, after_second

    _, first, snapshot, too_early, second, after_second = asyncio.run(main())
    assert first.outcome is WorkOutcome.RETRIED
    assert snapshot["status"] == JobStatus.PENDING
    assert snapshot["attempts"] == 1
    assert snapshot["last_error"] == "RuntimeError"
    assert "alice" not in str(snapshot)
    assert "lease_token" not in snapshot
    assert too_early is None
    assert second.outcome is WorkOutcome.RETRIED
    assert after_second["attempts"] == 2
    # Base 2 s after the first failure, then 4 s after the second (which happened at +2 s).
    assert snapshot["available_at"] == published_at + timedelta(seconds=2)
    assert after_second["available_at"] == published_at + timedelta(seconds=2 + 4)


def test_an_exhausted_job_moves_to_dead_letters() -> None:
    bus, db, clock = make_bus()

    async def main() -> list[Any]:
        job_id = await _publish(bus)
        outcomes = []
        for _ in range(FAST.max_attempts):
            outcomes.append(await bus.worker({CONSUMER: _fail}).run_once())
            clock.advance(FAST.backoff_max_seconds)
        assert await bus.worker({CONSUMER: _fail}).run_once() is None
        return [job_id, *outcomes]

    job_id, *outcomes = asyncio.run(main())
    assert [o.outcome for o in outcomes] == [WorkOutcome.RETRIED, WorkOutcome.RETRIED, WorkOutcome.DEAD_LETTERED]
    assert db["jobs"].docs == []
    [dead] = db["dead_letters"].docs
    assert dead["_id"] == job_id
    assert dead["org_id"] == ORG_A
    assert dead["reason"] == DeadLetterReason.EXHAUSTED
    assert dead["attempts"] == FAST.max_attempts
    assert dead["last_error"] == "RuntimeError"
    assert dead["dead_lettered_at"] == clock.now - timedelta(seconds=FAST.backoff_max_seconds)
    assert "lease_token" not in dead


def _crash(bus: Any, worker_id: str = "crashed") -> Any:
    """Start a worker on the job and kill it mid-handler: no release, no heartbeat any more."""

    async def crash() -> None:
        started = asyncio.Event()

        async def hang(job: JobContext) -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(bus.worker({CONSUMER: hang}, worker_id=worker_id).run_once())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    return crash()


def test_a_crashed_workers_lease_expires_and_the_job_is_retried() -> None:
    bus, db, clock = make_bus()
    seen: list[int] = []

    async def record(job: JobContext) -> None:
        seen.append(job.attempt)

    async def main() -> tuple[Any, Any, str]:
        job_id = await _publish(bus)
        await _crash(bus)
        held = _job(db, job_id)
        assert held["status"] == JobStatus.RUNNING
        assert held["lease_owner"] == "crashed"
        before_expiry = await bus.worker({CONSUMER: record}, worker_id="w2").run_once()
        clock.advance(FAST.lease_seconds)
        after_expiry = await bus.worker({CONSUMER: record}, worker_id="w2").run_once()
        return before_expiry, after_expiry, job_id

    before_expiry, after_expiry, job_id = asyncio.run(main())
    assert before_expiry is None
    assert after_expiry.outcome is WorkOutcome.COMPLETED
    assert seen == [2]
    assert _job(db, job_id)["status"] == JobStatus.DONE


def test_a_job_whose_last_attempt_crashed_is_dead_lettered_without_running() -> None:
    bus, db, clock = make_bus(settings=replace(FAST, max_attempts=1))
    ran: list[str] = []

    async def record(job: JobContext) -> None:
        ran.append(job.job_id)

    async def main() -> Any:
        await _publish(bus)
        await _crash(bus)
        clock.advance(FAST.lease_seconds)
        return await bus.worker({CONSUMER: record}).run_once()

    processed = asyncio.run(main())
    assert processed.outcome is WorkOutcome.DEAD_LETTERED
    assert ran == []
    [dead] = db["dead_letters"].docs
    assert dead["reason"] == DeadLetterReason.EXHAUSTED
    assert dead["last_error"] == "lease expired"


def test_a_heartbeat_keeps_extending_the_lease_while_the_handler_runs() -> None:
    bus, db, clock = make_bus()
    leases: list[Any] = []

    async def long_running(job: JobContext) -> None:
        for _ in range(3):
            clock.advance(FAST.lease_seconds / 2)
            await asyncio.sleep(FAST.heartbeat_seconds * 5)
            leases.append(_job(db, job.job_id)["locked_until"])

    async def main() -> Any:
        await _publish(bus)
        return await bus.worker({CONSUMER: long_running}).run_once()

    processed = asyncio.run(main())
    assert processed.outcome is WorkOutcome.COMPLETED
    # Each check sees a lease renewed from the advanced clock, so it never lapsed.
    assert leases[-1] == clock.now + timedelta(seconds=FAST.lease_seconds)


def test_a_worker_that_lost_its_lease_stops_its_handler_and_writes_nothing() -> None:
    bus, db, clock = make_bus()
    cancelled: list[str] = []
    started = asyncio.Event()

    async def slow(job: JobContext) -> None:
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(job.job_id)
            raise

    async def main() -> tuple[Any, Any, str]:
        job_id = await _publish(bus)
        stale = asyncio.create_task(bus.worker({CONSUMER: slow}, worker_id="stale").run_once())
        await started.wait()
        clock.advance(FAST.lease_seconds + 1)  # the stale worker's lease lapses (e.g. a long GC pause)
        fresh = await bus.worker({CONSUMER: _ok}, worker_id="fresh").run_once()
        return await stale, fresh, job_id

    stale, fresh, job_id = asyncio.run(main())
    assert fresh.outcome is WorkOutcome.COMPLETED
    assert stale.outcome is WorkOutcome.LEASE_LOST
    assert cancelled == [job_id]
    job = _job(db, job_id)
    assert job["status"] == JobStatus.DONE
    assert job["attempts"] == 2


def test_a_failure_after_the_lease_was_lost_is_not_recorded() -> None:
    slow_heartbeat = replace(FAST, heartbeat_seconds=10.0)
    bus, db, clock = make_bus(settings=slow_heartbeat)

    async def fail_late(job: JobContext) -> None:
        # Another worker takes the job over while this handler still runs, then this one fails.
        clock.advance(FAST.lease_seconds + 1)
        await bus.worker({CONSUMER: _ok}, worker_id="fresh").run_once()
        raise RuntimeError("late")

    async def main() -> Any:
        await _publish(bus)
        return await bus.worker({CONSUMER: fail_late}, worker_id="stale").run_once()

    processed = asyncio.run(main())
    assert processed.outcome is WorkOutcome.LEASE_LOST
    assert db["jobs"].docs[0]["status"] == JobStatus.DONE
    assert db["dead_letters"].docs == []


# --- box 5: a job without an org is dead-lettered --------------------------------------------


def _raw_job(org: Any = None, **extra: Any) -> dict[str, Any]:
    clock = Clock()
    job: dict[str, Any] = {
        "_id": "raw-1",
        "consumer": CONSUMER,
        "event_type": EVENT,
        "event_id": "e1",
        "payload": {},
        "status": JobStatus.PENDING,
        "attempts": 0,
        "max_attempts": 3,
        "available_at": clock.now,
        **extra,
    }
    if org is not None:
        job["org_id"] = org
    return job


@pytest.mark.parametrize(
    ("org", "reason"),
    [
        (None, DeadLetterReason.MISSING_ORG_ID),
        ("", DeadLetterReason.INVALID_ORG_ID),
        ("org$x", DeadLetterReason.INVALID_ORG_ID),
        ({"$ne": None}, DeadLetterReason.INVALID_ORG_ID),
    ],
    ids=["missing", "empty", "operator-chars", "mapping"],
)
def test_a_job_without_a_valid_org_id_is_dead_lettered_and_never_run(org: Any, reason: str) -> None:
    bus, db, _ = make_bus()
    ran: list[str] = []

    async def record(job: JobContext) -> None:
        ran.append(job.job_id)

    async def main() -> Any:
        await db["jobs"].insert_one(_raw_job(org))
        return await bus.worker({CONSUMER: record}).run_once()

    processed = asyncio.run(main())
    assert processed.outcome is WorkOutcome.DEAD_LETTERED
    assert ran == []
    assert db["jobs"].docs == []
    [dead] = db["dead_letters"].docs
    assert dead["_id"] == "raw-1"
    assert dead["reason"] == reason
    assert ("org_id" in dead) is (org is not None)


def test_a_job_with_a_bad_payload_is_dead_lettered() -> None:
    bus, db, _ = make_bus()

    async def main() -> Any:
        await db["jobs"].insert_one(_raw_job(ORG_A, payload="not a mapping"))
        return await bus.worker({CONSUMER: _ok}).run_once()

    processed = asyncio.run(main())
    assert processed.outcome is WorkOutcome.DEAD_LETTERED
    assert db["dead_letters"].docs[0]["reason"] == DeadLetterReason.MALFORMED


# --- the run loop --------------------------------------------------------------------------


def test_run_processes_until_stopped_and_reports_the_count() -> None:
    bus, db, _ = make_bus()

    async def main() -> int:
        for i in range(3):
            await _publish(bus, key=f"k{i}")
        stop = asyncio.Event()
        worker = bus.worker({CONSUMER: _ok})

        async def stop_when_done() -> None:
            while any(d["status"] != JobStatus.DONE for d in db["jobs"].docs):
                await asyncio.sleep(0.005)
            stop.set()

        count, _ = await asyncio.gather(worker.run(stop), stop_when_done())
        return int(count)

    assert asyncio.run(main()) == 3


def test_run_keeps_going_after_an_unexpected_worker_error() -> None:
    bus, db, _ = make_bus()
    failures = [ConnectionError("blip")]

    async def flaky_write() -> None:
        if failures:
            raise failures.pop()

    async def main() -> int:
        await _publish(bus)
        db.write_hook = flaky_write
        stop = asyncio.Event()

        async def stop_soon() -> None:
            while db["jobs"].docs[0]["status"] != JobStatus.DONE:
                await asyncio.sleep(0.005)
            stop.set()

        count, _ = await asyncio.gather(bus.worker({CONSUMER: _ok}).run(stop), stop_soon())
        return int(count)

    assert asyncio.run(main()) == 1
