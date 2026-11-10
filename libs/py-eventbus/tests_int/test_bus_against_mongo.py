"""The event bus against the compose MongoDB replica set (EVT-1 boxes 1-5; PRD §7.2, §7.9, §12).

Workers run on their own clients, threads and event loops, as separate processes would. Everything
is read back with the root client, so no test trusts the bus to report on itself. Each test uses
its own scratch database.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_data import DataStore, TenantViolationError, connect, tenant_scope
from onb_eventbus import (
    DeadLetterReason,
    JobContext,
    JobStatus,
    MongoEventBus,
    PublishResult,
    QueueSettings,
    WorkOutcome,
)
from onb_telemetry.testing import captured_telemetry
from opentelemetry import trace
from pymongo.errors import DuplicateKeyError

from .conftest import Scratch

ORG_A = "org_alpha"
ORG_B = "org_beta"
EVENT = "run.started"
CONSUMER = "orchestrator.start_run"
SUBSCRIPTIONS = {EVENT: [CONSUMER]}

QUICK = QueueSettings(
    lease_seconds=1.0,
    heartbeat_seconds=0.25,
    max_attempts=2,
    backoff_base_seconds=0.05,
    backoff_max_seconds=0.2,
    poll_seconds=0.05,
)


def run_with_bus[T](
    scratch: Scratch,
    body: Callable[[MongoEventBus], Coroutine[Any, Any, T]],
    settings: QueueSettings = QUICK,
) -> T:
    """Run `body` on a fresh client and event loop (one per call, like one process)."""

    async def main() -> T:
        store = connect(scratch.uri, scratch.name)
        try:
            bus = MongoEventBus(store, subscriptions=SUBSCRIPTIONS, settings=settings)
            await bus.ensure_indexes()
            return await body(bus)
        finally:
            await store.close()

    return asyncio.run(main())


async def publish(bus: MongoEventBus, org: str, key: str, **kwargs: Any) -> PublishResult:
    with tenant_scope(org):
        return await bus.publish(EVENT, {"run_id": key}, idempotency_key=key, run_id=key, **kwargs)


def jobs(scratch: Scratch, **query: Any) -> list[dict[str, Any]]:
    return list(scratch.db.jobs.find(query))


async def _ok(job: JobContext) -> None:
    return None


async def _fail(job: JobContext) -> None:
    raise RuntimeError("handler failed")


# --- box 1 ---------------------------------------------------------------------------------


@dataclass
class Ledger:
    """What the handlers saw, across threads."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    active: set[str] = field(default_factory=set)
    overlaps: list[str] = field(default_factory=list)
    runs: Counter[str] = field(default_factory=Counter)
    by_worker: Counter[str] = field(default_factory=Counter)

    def enter(self, job_id: str, worker: str) -> None:
        with self.lock:
            if job_id in self.active:
                self.overlaps.append(job_id)
            self.active.add(job_id)
            self.runs[job_id] += 1
            self.by_worker[worker] += 1

    def leave(self, job_id: str) -> None:
        with self.lock:
            self.active.discard(job_id)


def test_two_workers_never_process_the_same_job_at_once(scratch: Scratch) -> None:
    """Box 1. 60 jobs, 6 workers on 6 clients and threads, all claiming at the same moment."""
    settings = QueueSettings(lease_seconds=30.0, heartbeat_seconds=5.0, poll_seconds=0.05)

    async def seed(bus: MongoEventBus) -> list[str]:
        return [(await publish(bus, ORG_A, f"run-{i}")).job_ids[0] for i in range(60)]

    job_ids = run_with_bus(scratch, seed, settings)
    ledger = Ledger()
    barrier = threading.Barrier(6)

    def drain(worker_id: str) -> int:
        async def body(bus: MongoEventBus) -> int:
            async def handler(job: JobContext) -> None:
                ledger.enter(job.job_id, worker_id)
                await asyncio.sleep(0.01)  # hold the job long enough for others to try for it
                ledger.leave(job.job_id)

            worker = bus.worker({CONSUMER: handler}, worker_id=worker_id)
            barrier.wait()
            done = 0
            while await worker.run_once() is not None:
                done += 1
            return done

        return run_with_bus(scratch, body, settings)

    with ThreadPoolExecutor(max_workers=6) as pool:
        counts = list(pool.map(drain, [f"w{i}" for i in range(6)]))

    assert ledger.overlaps == []
    assert sorted(ledger.runs) == sorted(job_ids)
    assert set(ledger.runs.values()) == {1}, "each job ran exactly once"
    assert sum(counts) == 60
    assert len([c for c in counts if c > 0]) >= 2, f"work was not shared: {counts}"
    assert {j["status"] for j in jobs(scratch)} == {JobStatus.DONE.value}
    assert {j["attempts"] for j in jobs(scratch)} == {1}


def test_one_job_raced_by_eight_workers_is_claimed_once(scratch: Scratch) -> None:
    """Box 1, sharpened: eight claims for a single job released at once; exactly one wins."""
    run_with_bus(scratch, lambda bus: publish(bus, ORG_A, "only"))
    barrier = threading.Barrier(8)
    started = Counter[str]()
    lock = threading.Lock()

    def claim(worker_id: str) -> str | None:
        async def body(bus: MongoEventBus) -> str | None:
            async def handler(job: JobContext) -> None:
                with lock:
                    started[worker_id] += 1
                await asyncio.sleep(0.2)

            worker = bus.worker({CONSUMER: handler}, worker_id=worker_id)
            barrier.wait()
            done = await worker.run_once()
            return None if done is None else done.outcome.value

        return run_with_bus(scratch, body)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(claim, [f"w{i}" for i in range(8)]))

    assert sorted(o for o in outcomes if o is not None) == [WorkOutcome.COMPLETED.value]
    assert sum(started.values()) == 1
    [job] = jobs(scratch)
    assert job["attempts"] == 1


# --- box 2 ---------------------------------------------------------------------------------


def test_a_crashed_workers_lease_expires_and_the_job_is_retried(scratch: Scratch) -> None:
    run_with_bus(scratch, lambda bus: publish(bus, ORG_A, "r1"))

    async def crash(bus: MongoEventBus) -> None:
        started = asyncio.Event()

        async def hang(job: JobContext) -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(bus.worker({CONSUMER: hang}, worker_id="crashed").run_once())
        await started.wait()
        task.cancel()  # the process dies: no release, no more heartbeats
        with pytest.raises(asyncio.CancelledError):
            await task

    run_with_bus(scratch, crash)
    [held] = jobs(scratch)
    assert held["status"] == JobStatus.RUNNING.value
    assert held["lease_owner"] == "crashed"

    attempts_seen: list[int] = []

    async def record(job: JobContext) -> None:
        attempts_seen.append(job.attempt)

    async def take_over(bus: MongoEventBus) -> tuple[Any, Any]:
        worker = bus.worker({CONSUMER: record}, worker_id="survivor")
        early = await worker.run_once()
        lapse = (held["locked_until"] - datetime.now(UTC)).total_seconds()
        await asyncio.sleep(max(lapse, 0) + 0.1)
        return early, await worker.run_once()

    early, late = run_with_bus(scratch, take_over)
    assert early is None, "the job was taken while the crashed worker's lease still held"
    assert late.outcome is WorkOutcome.COMPLETED
    assert attempts_seen == [2]
    [job] = jobs(scratch)
    assert job["status"] == JobStatus.DONE.value
    assert job["attempts"] == 2


def test_a_live_heartbeat_keeps_the_job_from_other_workers(scratch: Scratch) -> None:
    run_with_bus(scratch, lambda bus: publish(bus, ORG_A, "r1"))

    async def body(bus: MongoEventBus) -> tuple[Any, list[Any]]:
        started = asyncio.Event()

        async def long_job(job: JobContext) -> None:
            started.set()
            await asyncio.sleep(QUICK.lease_seconds * 2.5)

        holder = asyncio.create_task(bus.worker({CONSUMER: long_job}, worker_id="holder").run_once())
        await started.wait()
        rival = bus.worker({CONSUMER: _ok}, worker_id="rival")
        rival_tries = []
        for _ in range(5):
            await asyncio.sleep(QUICK.lease_seconds / 2)
            rival_tries.append(await rival.run_once())
        return await holder, rival_tries

    holder, rival_tries = run_with_bus(scratch, body)
    assert rival_tries == [None] * 5
    assert holder.outcome is WorkOutcome.COMPLETED
    [job] = jobs(scratch)
    assert job["attempts"] == 1


def test_an_exhausted_job_moves_to_dead_letters(scratch: Scratch) -> None:
    result = run_with_bus(scratch, lambda bus: publish(bus, ORG_A, "r1"))

    async def keep_failing(bus: MongoEventBus) -> list[WorkOutcome]:
        worker = bus.worker({CONSUMER: _fail})
        outcomes: list[WorkOutcome] = []
        deadline = time.monotonic() + 10
        while WorkOutcome.DEAD_LETTERED not in outcomes and time.monotonic() < deadline:
            done = await worker.run_once()
            if done is None:
                await asyncio.sleep(0.02)
            else:
                outcomes.append(done.outcome)
        return outcomes

    outcomes = run_with_bus(scratch, keep_failing)
    assert outcomes == [WorkOutcome.RETRIED, WorkOutcome.DEAD_LETTERED]
    assert jobs(scratch) == []
    [dead] = scratch.db.dead_letters.find()
    assert dead["_id"] == result.job_ids[0]
    assert dead["org_id"] == ORG_A
    assert dead["reason"] == DeadLetterReason.EXHAUSTED.value
    assert dead["attempts"] == 2
    assert dead["last_error"] == "RuntimeError"
    assert "lease_token" not in dead


def test_a_job_whose_last_attempt_crashed_is_dead_lettered(scratch: Scratch) -> None:
    once = QueueSettings(lease_seconds=0.5, heartbeat_seconds=0.1, max_attempts=1, poll_seconds=0.05)
    run_with_bus(scratch, lambda bus: publish(bus, ORG_A, "r1"), once)

    async def crash_then_reclaim(bus: MongoEventBus) -> Any:
        started = asyncio.Event()

        async def hang(job: JobContext) -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(bus.worker({CONSUMER: hang}).run_once())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(once.lease_seconds + 0.2)
        return await bus.worker({CONSUMER: _ok}).run_once()

    processed = run_with_bus(scratch, crash_then_reclaim, once)
    assert processed.outcome is WorkOutcome.DEAD_LETTERED
    [dead] = scratch.db.dead_letters.find()
    assert (dead["reason"], dead["last_error"]) == (DeadLetterReason.EXHAUSTED.value, "lease expired")


# --- box 3 ---------------------------------------------------------------------------------


def test_a_duplicate_idempotency_key_is_ignored(scratch: Scratch) -> None:
    async def twice(bus: MongoEventBus) -> tuple[PublishResult, PublishResult]:
        return await publish(bus, ORG_A, "k1"), await publish(bus, ORG_A, "k1")

    first, second = run_with_bus(scratch, twice)
    assert not first.duplicate
    assert second.duplicate
    assert second.event_id == first.event_id
    assert scratch.db.events.count_documents({}) == 1
    assert scratch.db.jobs.count_documents({}) == 1


def test_concurrent_publishes_with_one_key_write_one_event(scratch: Scratch) -> None:
    async def race(bus: MongoEventBus) -> list[PublishResult]:
        return list(await asyncio.gather(*(publish(bus, ORG_A, "same-key") for _ in range(10))))

    results = run_with_bus(scratch, race)
    assert [r.duplicate for r in results].count(False) == 1
    assert len({r.event_id for r in results}) == 1
    assert scratch.db.events.count_documents({}) == 1
    assert scratch.db.jobs.count_documents({}) == 1


def test_concurrent_publishes_with_one_key_apply_the_domain_change_once(scratch: Scratch) -> None:
    """Review finding: retried or racing transactions must not leave the change twice."""

    async def race(bus: MongoEventBus) -> list[PublishResult]:
        async def change() -> None:
            await bus.store.tenant("onboarding_runs").update_one(
                {"_id": "run-1"}, {"$inc": {"starts": 1}, "$set": {"state": "started"}}, upsert=True
            )

        return list(await asyncio.gather(*(publish(bus, ORG_A, "same-key", change=change) for _ in range(10))))

    results = run_with_bus(scratch, race)
    assert [r.duplicate for r in results].count(False) == 1
    assert scratch.db.onboarding_runs.find_one({"_id": "run-1"}) == {
        "_id": "run-1",
        "org_id": ORG_A,
        "starts": 1,
        "state": "started",
    }
    assert scratch.db.events.count_documents({}) == 1


def test_a_job_with_a_corrupt_attempt_count_does_not_block_the_queue(scratch: Scratch) -> None:
    """Review finding: `$inc` on a non-number fails on the server, so such a job is never claimed."""
    scratch.db.jobs.insert_one(
        {
            "_id": "corrupt",
            "org_id": ORG_A,
            "consumer": CONSUMER,
            "status": JobStatus.PENDING.value,
            "attempts": None,
            "max_attempts": 3,
            "available_at": datetime(2000, 1, 1, tzinfo=UTC),
        }
    )

    async def body(bus: MongoEventBus) -> tuple[Any, Any, str]:
        good = (await publish(bus, ORG_A, "r1")).job_ids[0]
        worker = bus.worker({CONSUMER: _ok})
        return await worker.run_once(), await worker.run_once(), good

    first, second, good = run_with_bus(scratch, body)
    assert first is not None
    assert first.job_id == good
    assert second is None
    assert scratch.db.jobs.find_one({"_id": "corrupt"}, {"status": 1}) == {"_id": "corrupt", "status": "pending"}


def test_the_key_is_unique_per_org_not_globally(scratch: Scratch) -> None:
    async def both(bus: MongoEventBus) -> tuple[PublishResult, PublishResult]:
        return await publish(bus, ORG_A, "k1"), await publish(bus, ORG_B, "k1")

    a, b = run_with_bus(scratch, both)
    assert not a.duplicate
    assert not b.duplicate
    assert sorted(str(e["org_id"]) for e in scratch.db.events.find()) == [ORG_A, ORG_B]
    with pytest.raises(DuplicateKeyError):
        scratch.db.events.insert_one({"org_id": ORG_A, "idempotency_key": "k1"})


def test_a_rolled_back_transaction_leaves_neither_change_nor_event(scratch: Scratch) -> None:
    async def failing_change(bus: MongoEventBus) -> None:
        async def change() -> None:
            await bus.store.tenant("onboarding_runs").insert_one({"_id": "run-1", "state": "started"})
            raise RuntimeError("a domain rule failed after the write")

        await publish(bus, ORG_A, "k1", change=change)

    with pytest.raises(RuntimeError, match="domain rule"):
        run_with_bus(scratch, failing_change)
    assert scratch.db.onboarding_runs.count_documents({}) == 0
    assert scratch.db.events.count_documents({}) == 0
    assert scratch.db.jobs.count_documents({}) == 0


def test_a_server_side_write_error_rolls_back_the_change_and_the_event(scratch: Scratch) -> None:
    scratch.db.onboarding_runs.insert_one({"_id": "taken", "org_id": ORG_A})

    async def conflicting_change(bus: MongoEventBus) -> None:
        async def change() -> None:
            runs = bus.store.tenant("onboarding_runs")
            await runs.insert_one({"_id": "run-2", "state": "started"})
            await runs.insert_one({"_id": "taken"})  # duplicate _id: the server refuses it

        await publish(bus, ORG_A, "k1", change=change)

    with pytest.raises(DuplicateKeyError):
        run_with_bus(scratch, conflicting_change)
    assert [d["_id"] for d in scratch.db.onboarding_runs.find()] == ["taken"]
    assert scratch.db.events.count_documents({}) == 0
    assert scratch.db.jobs.count_documents({}) == 0


def test_a_committed_publish_writes_change_event_and_jobs_together(scratch: Scratch) -> None:
    async def ok(bus: MongoEventBus) -> PublishResult:
        async def change() -> None:
            await bus.store.tenant("onboarding_runs").insert_one({"_id": "run-1", "state": "started"})

        return await publish(bus, ORG_A, "k1", change=change)

    result = run_with_bus(scratch, ok)
    assert scratch.db.onboarding_runs.find_one({"_id": "run-1"}) == {
        "_id": "run-1",
        "state": "started",
        "org_id": ORG_A,
    }
    [event] = scratch.db.events.find()
    [job] = scratch.db.jobs.find()
    assert event["_id"] == result.event_id
    assert set(event) == {
        "_id", "org_id", "type", "payload", "run_id", "idempotency_key", "traceparent", "created_at",
    }  # fmt: skip
    assert (job["org_id"], job["event_id"], job["status"]) == (ORG_A, result.event_id, JobStatus.PENDING.value)


# --- box 4 ---------------------------------------------------------------------------------


def test_a_consumer_span_joins_the_producer_trace(scratch: Scratch) -> None:
    with captured_telemetry("evt-1-int") as captured:

        async def produce(bus: MongoEventBus) -> None:
            with trace.get_tracer("test.api").start_as_current_span("POST /runs"):
                await publish(bus, ORG_A, "r1")

        run_with_bus(scratch, produce)
        handler_trace: list[int] = []

        async def handler(job: JobContext) -> None:
            handler_trace.append(trace.get_current_span().get_span_context().trace_id)

        def consume_elsewhere() -> Any:
            # Another thread and event loop with no current span, as in the worker process.
            async def body(bus: MongoEventBus) -> Any:
                assert not trace.get_current_span().get_span_context().is_valid
                return await bus.worker({CONSUMER: handler}).run_once()

            return run_with_bus(scratch, body)

        with ThreadPoolExecutor(max_workers=1) as pool:
            processed = pool.submit(consume_elsewhere).result()
        spans = {span.name: span for span in captured.spans()}

    assert processed.outcome is WorkOutcome.COMPLETED
    request, published, consumed = spans["POST /runs"], spans[f"publish {EVENT}"], spans[f"consume {CONSUMER}"]
    assert published.context.trace_id == request.context.trace_id
    assert consumed.context.trace_id == request.context.trace_id
    assert consumed.parent is not None
    assert consumed.parent.span_id == published.context.span_id
    assert handler_trace == [request.context.trace_id]
    [job] = jobs(scratch)
    assert format(request.context.trace_id, "032x") in job["traceparent"]


# --- box 5 ---------------------------------------------------------------------------------


def test_a_job_without_org_id_is_dead_lettered(scratch: Scratch) -> None:
    now = datetime.now(UTC)
    scratch.db.jobs.insert_one(
        {
            "_id": "orphan",
            "consumer": CONSUMER,
            "event_id": "e1",
            "event_type": EVENT,
            "payload": {},
            "status": JobStatus.PENDING.value,
            "attempts": 0,
            "max_attempts": 3,
            "available_at": now,
        }
    )
    ran: list[str] = []

    async def record(job: JobContext) -> None:
        ran.append(job.job_id)

    processed = run_with_bus(scratch, lambda bus: bus.worker({CONSUMER: record}).run_once())
    assert processed is not None
    assert processed.outcome is WorkOutcome.DEAD_LETTERED
    assert ran == []
    assert jobs(scratch) == []
    [dead] = scratch.db.dead_letters.find()
    assert dead["_id"] == "orphan"
    assert dead["reason"] == DeadLetterReason.MISSING_ORG_ID.value
    assert "org_id" not in dead


# --- the consumer side of tenant isolation (PRD §7.9 Testing) -------------------------------


def test_a_consumer_sees_only_the_org_of_its_job(scratch: Scratch) -> None:
    scratch.db.hires.insert_many(
        [{"_id": "a1", "org_id": ORG_A}, {"_id": "a2", "org_id": ORG_A}, {"_id": "b1", "org_id": ORG_B}]
    )
    seen: dict[str, list[str]] = {}
    refused: list[str] = []

    async def body(bus: MongoEventBus) -> None:
        store: DataStore = bus.store

        async def handler(job: JobContext) -> None:
            hires = store.tenant("hires")
            seen[job.org_id] = sorted(d["_id"] for d in await hires.find())
            other = ORG_B if job.org_id == ORG_A else ORG_A
            try:
                await hires.find({"org_id": other})
            except TenantViolationError:
                refused.append(job.org_id)
            if job.org_id == ORG_A:
                assert await hires.find_one({"_id": "b1"}) is None

        await publish(bus, ORG_A, "ka")
        await publish(bus, ORG_B, "kb")
        worker = bus.worker({CONSUMER: handler})
        assert await worker.run_once() is not None
        assert await worker.run_once() is not None

    run_with_bus(scratch, body)
    assert seen == {ORG_A: ["a1", "a2"], ORG_B: ["b1"]}
    assert sorted(refused) == [ORG_A, ORG_B]
