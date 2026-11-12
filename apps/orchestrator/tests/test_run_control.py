# ruff: noqa: S105, S106
"""Retry, blocking, control and recovery in the orchestrator (RUN-2; PRD FR-R4, FR-R5, FR-R6).

Over the real `DataStore` with the in-memory driver; the bus is a recording stand-in that runs `change`
like the outbox. The same recovery against a real worker process killed with SIGKILL is in
`tests_int/test_kill_and_restart.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.runs import (
    RUN_RESUMED,
    RUN_STARTED,
    START_RUN,
    RunStarter,
    RunState,
    RunStore,
    TaskState,
)
from onb_agents.runs.testing import OutboxStandIn, diamond_template, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.notify import RUN_BLOCKED
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext, ReleaseJob
from onb_orchestrator.checkpointer import CHECKPOINT_WRITES, CHECKPOINTS
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor, TaskWork
from onb_orchestrator.policy import RunPolicy
from onb_orchestrator.runner import TaskStepError

ORG = "org_a"


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


NO_WAIT = RunPolicy(max_attempts=3, backoff_base=0.0, backoff_max=0.0, lease_seconds=30.0)


@dataclass
class World:
    db: MemoryDatabase
    store: DataStore
    runs: RunStore
    bus: OutboxStandIn = field(default_factory=OutboxStandIn)
    now: datetime = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)

    def clock(self) -> datetime:
        return self.now

    async def start(self, role: str = "engineer") -> str:
        starter = RunStarter(self.runs, self.bus, InMemoryAuditLog())
        with tenant_scope(ORG):
            started = await starter.start(
                template=diamond_template(), hire_id="h_1", variables=hire_variables_for(role), actor_id="u_hr"
            )
        return str(started.run["_id"])

    async def drive(
        self,
        run_id: str,
        executor: StubExecutor,
        *,
        policy: RunPolicy = NO_WAIT,
        drain: asyncio.Event | None = None,
        event: str = RUN_STARTED,
        sleep: Any = None,
    ) -> None:
        handler = start_run_handler(
            self.store, executor, bus=self.bus, policy=policy, drain=drain, clock=self.clock, sleep=sleep
        )
        consumer = START_RUN if event == RUN_STARTED else "orchestrator.resume_run"
        job = JobContext("job_1", ORG, consumer, "ev_1", event, MappingProxyType({"run_id": run_id}), run_id, 1)
        with tenant_scope(ORG):
            await handler(job)

    async def run_state(self, run_id: str) -> tuple[str, dict[str, dict[str, Any]]]:
        with tenant_scope(ORG):
            found = await self.runs.get(run_id)
            tasks = {t["task_id"]: t for t in await self.runs.tasks(run_id)}
        assert found is not None
        return str(found["status"]), tasks

    async def update_task(self, run_id: str, task_id: str, **fields: Any) -> None:
        with tenant_scope(ORG):
            await self.store.tenant("run_tasks").update_one({"run_id": run_id, "task_id": task_id}, {"$set": fields})

    def checkpoints(self) -> int:
        return len(self.db.raw(CHECKPOINTS)) + len(self.db.raw(CHECKPOINT_WRITES))


def _world() -> World:
    db = MemoryDatabase()
    store = memory_store(db)
    return World(db, store, RunStore(store))


def statuses(tasks: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {task_id: str(task["status"]) for task_id, task in tasks.items()}


# --- box 2: retry with backoff, then block the run and tell HR -----------------------------------


def test_a_failing_task_is_retried_with_backoff_and_then_succeeds() -> None:
    world = _world()
    executor = StubExecutor(fail_times={"b": 2})
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        policy = RunPolicy(max_attempts=3, backoff_base=2.0, backoff_max=60.0, lease_seconds=30.0)
        await world.drive(run_id, executor, policy=policy, sleep=sleep)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "completed"
    assert tasks["b"]["status"] == "done"
    assert tasks["b"]["attempts"] == 3
    assert executor.calls.count("b") == 3
    assert slept == [2.0, 4.0], "the wait doubles after each failed attempt"
    assert [(h["from"], h["to"]) for h in tasks["b"]["history"]].count(("running", "failed")) == 2


def test_a_task_failing_past_its_max_attempts_blocks_the_run_and_records_an_hr_notification() -> None:
    world = _world()
    executor = StubExecutor(fail={"b"})

    async def go() -> tuple[str, dict[str, dict[str, Any]], str]:
        run_id = await world.start()
        await world.drive(run_id, executor)
        status, tasks = await world.run_state(run_id)
        return status, tasks, run_id

    status, tasks, run_id = run(go())
    assert status == "blocked"
    assert tasks["b"]["status"] == "failed"
    assert tasks["b"]["attempts"] == 3
    assert tasks["b"]["error"] == "StubTaskError", "the error type is kept, never a message"
    assert executor.calls.count("b") == 3
    assert tasks["d"]["status"] == "pending", "tasks after the failed one are not cancelled: HR can retry"
    blocked = [e for e in world.bus.published if e["type"] == RUN_BLOCKED]
    assert len(blocked) == 1
    assert blocked[0]["payload"] == {"run_id": run_id, "task_id": "b"}
    assert blocked[0]["org_id"] == ORG


def test_a_blocked_run_keeps_its_checkpoint_and_a_siblings_lease_is_released() -> None:
    world = _world()
    executor = StubExecutor(fail={"b"}, delays={"c": 5.0})

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "blocked"
    assert world.checkpoints() > 0
    assert tasks["c"]["lease_token"] is None, "a sibling cut off mid-step gives its lease back"
    assert tasks["c"]["attempts"] == 0, "and the attempt it did not get to finish"


def test_hr_retrying_the_failed_task_lets_the_blocked_run_finish() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, dict[str, Any]], StubExecutor]:
        run_id = await world.start()
        await world.drive(run_id, StubExecutor(fail={"b"}))
        # What `POST /runs/{id}/tasks/b/retry` does: the task goes back to ready with a fresh attempt count
        # and the run to running; the resume event is delivered to the orchestrator.
        with tenant_scope(ORG):
            await world.runs.move_task(run_id, "b", TaskState.READY, at=world.now, fields={"attempts": 0})
            await world.runs.move_run(run_id, RunState.RUNNING, at=world.now, expected_status=RunState.BLOCKED)
        healthy = StubExecutor()
        await world.drive(run_id, healthy, event=RUN_RESUMED)
        status, tasks = await world.run_state(run_id)
        return status, tasks, healthy

    status, tasks, healthy = run(go())
    assert status == "completed"
    assert statuses(tasks) == {"a": "done", "b": "done", "c": "done", "d": "done", "e": "done", "f": "done"}
    assert "a" not in healthy.calls, "a task that was done is not run again by the resumed run"
    assert world.checkpoints() == 0, "the finished run's checkpoint is deleted"


def test_hr_completing_the_failed_task_by_hand_lets_the_blocked_run_finish_without_running_it() -> None:
    world = _world()

    async def go() -> tuple[str, StubExecutor]:
        run_id = await world.start()
        await world.drive(run_id, StubExecutor(fail={"b"}))
        with tenant_scope(ORG):
            await world.runs.move_task(run_id, "b", TaskState.DONE, at=world.now, by="u_hr")
            await world.runs.move_run(run_id, RunState.RUNNING, at=world.now, expected_status=RunState.BLOCKED)
        healthy = StubExecutor()
        await world.drive(run_id, healthy, event=RUN_RESUMED)
        status, _ = await world.run_state(run_id)
        return status, healthy

    status, healthy = run(go())
    assert status == "completed"
    assert "b" not in healthy.calls


def test_a_run_that_fails_for_a_deterministic_reason_is_failed_not_blocked_and_checkpoint_deleted() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.store.tenant("onboarding_runs").update_one({"_id": run_id}, {"$set": {"dag_hash": "bad"}})
        await world.drive(run_id, StubExecutor())
        return (await world.run_state(run_id))[0]

    assert run(go()) == "failed"
    assert world.checkpoints() == 0


# --- pause and cancel ---------------------------------------------------------------------------


def test_a_paused_run_starts_no_new_task_and_resumes_where_it_stopped() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, str], StubExecutor, str, dict[str, str]]:
        run_id = await world.start()
        executor = StubExecutor()
        original = executor.execute

        async def pause_after_a(work: TaskWork) -> None:
            await original(work)
            if work.task_id == "a":
                with tenant_scope(ORG):
                    await world.runs.move_run(run_id, RunState.PAUSED, at=world.now, by="u_hr")

        executor.execute = pause_after_a  # type: ignore[method-assign]
        await world.drive(run_id, executor)
        paused, tasks = await world.run_state(run_id)
        paused_states = statuses(tasks)
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.RUNNING, at=world.now, by="u_hr")
        resumed = StubExecutor()
        await world.drive(run_id, resumed, event=RUN_RESUMED)
        status, after = await world.run_state(run_id)
        return paused, paused_states, resumed, status, statuses(after)

    paused, paused_states, resumed, status, after = run(go())
    assert paused == "paused"
    assert paused_states["a"] == "done"
    assert {paused_states[t] for t in "bcdef"} <= {"pending", "ready"}, paused_states
    assert "a" not in resumed.calls
    assert status == "completed"
    assert set(after.values()) <= {"done", "skipped"}


def test_a_run_paused_before_it_started_is_left_alone() -> None:
    world = _world()

    async def go() -> tuple[str, StubExecutor]:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.PAUSED, at=world.now, by="u_hr")
        executor = StubExecutor()
        await world.drive(run_id, executor)
        return (await world.run_state(run_id))[0], executor

    status, executor = run(go())
    assert status == "paused"
    assert executor.calls == []


def test_a_cancelled_run_stops_and_its_checkpoint_is_deleted() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, str], StubExecutor]:
        run_id = await world.start()
        executor = StubExecutor(delays={"b": 0.2, "c": 0.2})
        original = executor.execute

        async def cancel_during_a(work: TaskWork) -> None:
            await original(work)
            if work.task_id == "a":  # what `POST /runs/{id}/cancel` does
                with tenant_scope(ORG):
                    await world.runs.move_run(run_id, RunState.CANCELLED, at=world.now, by="u_hr")
                    await world.runs.cancel_unfinished(run_id, at=world.now)

        executor.execute = cancel_during_a  # type: ignore[method-assign]
        await world.drive(run_id, executor)
        status, tasks = await world.run_state(run_id)
        return status, statuses(tasks), executor

    status, tasks, executor = run(go())
    assert status == "cancelled"
    assert "b" not in executor.calls
    assert "c" not in executor.calls
    assert not {t for t in tasks.values() if t in {"running", "ready", "pending"}}
    assert world.checkpoints() == 0


# --- box 1 (unit): taking over a task left running by a crashed worker ---------------------------


async def _left_running(world: World, run_id: str, task_id: str, *, until: datetime, attempts: int = 1) -> None:
    """What a worker killed mid-task leaves: the task `running`, its lease still in the database."""
    with tenant_scope(ORG):
        ready = await world.runs.move_task(run_id, task_id, TaskState.READY, at=world.now)
        await world.runs.move_task(
            run_id,
            task_id,
            TaskState.RUNNING,
            at=world.now,
            expected=ready,
            fields={"lease_token": "tok_dead", "lease_until": until, "attempts": attempts},
        )
        await world.runs.move_run(run_id, RunState.RUNNING, at=world.now)


def test_a_task_whose_holder_died_is_taken_over_once_its_lease_ran_out_and_runs_with_the_same_key() -> None:
    world = _world()
    executor = StubExecutor()

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await _left_running(world, run_id, "a", until=world.now - timedelta(seconds=1))
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "completed"
    assert executor.calls.count("a") == 1
    assert tasks["a"]["attempts"] == 2, "a takeover is another attempt"
    assert any(h.get("note") == "takeover" for h in tasks["a"]["history"])
    [key] = {w.idempotency_key for w in executor.work if w.task_id == "a"}
    assert key.startswith("run_"), key
    assert key.endswith(":a"), key


def test_a_task_another_worker_still_holds_is_not_taken_over_or_run() -> None:
    world = _world()
    executor = StubExecutor()

    async def go() -> None:
        run_id = await world.start()
        await _left_running(world, run_id, "a", until=world.now + timedelta(seconds=20))
        await world.drive(run_id, executor)

    with pytest.raises(TaskStepError) as in_progress:
        run(go())
    assert (in_progress.value.type_name, in_progress.value.transient) == ("TaskInProgressError", True)
    assert executor.calls == []


def test_a_task_that_keeps_crashing_workers_stops_at_its_max_attempts_and_blocks_the_run() -> None:
    world = _world()
    executor = StubExecutor()

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await _left_running(world, run_id, "a", until=world.now - timedelta(seconds=1), attempts=3)
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "blocked"
    assert tasks["a"]["status"] == "failed"
    assert tasks["a"]["error"] == "LeaseExpired"
    assert executor.calls == []


def test_a_worker_that_lost_its_lease_stops_its_task_and_does_not_finish_it() -> None:
    world = _world()
    cancelled = asyncio.Event()

    class Slow(StubExecutor):
        async def execute(self, work: TaskWork) -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def go() -> tuple[dict[str, dict[str, Any]], bool]:
        run_id = await world.start()
        policy = RunPolicy(max_attempts=3, backoff_base=0.0, backoff_max=0.0, lease_seconds=0.3)
        job = asyncio.create_task(world.drive(run_id, Slow(), policy=policy))
        await asyncio.sleep(0.15)
        # Another worker took the task over: the token in the database is no longer ours.
        await world.update_task(run_id, "a", lease_token="tok_other")
        with pytest.raises(TaskStepError) as in_progress:
            await asyncio.wait_for(job, timeout=3)
        assert in_progress.value.transient
        return (await world.run_state(run_id))[1], cancelled.is_set()

    tasks, was_cancelled = run(go())
    assert was_cancelled
    assert tasks["a"]["status"] == "running", "the loser writes nothing"
    assert tasks["a"]["lease_token"] == "tok_other"


def test_a_running_task_keeps_its_lease_alive_while_it_works() -> None:
    world = _world()

    class Slow(StubExecutor):
        async def execute(self, work: TaskWork) -> None:
            await asyncio.sleep(0.5)

    async def go() -> list[datetime]:
        run_id = await world.start()
        world.now = datetime.now(UTC)
        policy = RunPolicy(max_attempts=3, backoff_base=0.0, backoff_max=0.0, lease_seconds=0.3)
        seen: list[datetime] = []
        job = asyncio.create_task(world.drive(run_id, Slow(), policy=policy))
        for _ in range(4):
            await asyncio.sleep(0.1)
            world.now = datetime.now(UTC)
            task = (await world.run_state(run_id))[1]["a"]
            if task["status"] == "running":
                seen.append(task["lease_until"])
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        return seen

    seen = run(go())
    assert len(seen) >= 2
    assert seen[-1] > seen[0], "the heartbeat moved the lease forward"


# --- graceful drain -----------------------------------------------------------------------------


def test_a_draining_worker_finishes_the_task_in_hand_starts_no_more_and_releases_the_job() -> None:
    world = _world()
    drain = asyncio.Event()

    async def go() -> tuple[dict[str, str], str]:
        run_id = await world.start()
        executor = StubExecutor()
        original = executor.execute

        async def drain_during_a(work: TaskWork) -> None:
            if work.task_id == "a":
                drain.set()  # SIGTERM arrives while `a` is running
            await original(work)

        executor.execute = drain_during_a  # type: ignore[method-assign]
        with pytest.raises(ReleaseJob):
            await world.drive(run_id, executor, drain=drain)
        status, tasks = await world.run_state(run_id)
        return statuses(tasks), status

    tasks, status = run(go())
    assert tasks["a"] == "done", "the task in hand finished"
    assert not {tasks[t] for t in "bc"} & {"running", "done"}, "no new task started"
    assert status == "running", "the run stays running for the next worker"


def test_the_released_run_is_picked_up_by_the_next_worker_and_completes() -> None:
    world = _world()
    drain = asyncio.Event()
    drain.set()

    async def go() -> tuple[str, StubExecutor]:
        run_id = await world.start()
        with pytest.raises(ReleaseJob):
            await world.drive(run_id, StubExecutor(), drain=drain)
        second = StubExecutor()
        await world.drive(run_id, second)
        return (await world.run_state(run_id))[0], second

    status, second = run(go())
    assert status == "completed"
    assert second.calls.count("a") == 1


# --- review findings (code review of the RUN-2 diff) ---------------------------------------------


def test_a_pause_that_arrives_before_the_run_is_driven_is_not_overridden_by_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler read the run `started`; HR paused it; the start must not move `paused → running`."""
    world = _world()

    async def go() -> tuple[str, StubExecutor]:
        run_id = await world.start()
        executor = StubExecutor()
        handler = start_run_handler(world.store, executor, bus=world.bus, policy=NO_WAIT, clock=world.clock)
        original = RunStore.get
        paused_once: list[bool] = []

        async def pause_after_read(self: RunStore, rid: str) -> dict[str, Any] | None:
            found = await original(self, rid)
            if found is not None and found["status"] == "started" and not paused_once:
                paused_once.append(True)
                await self.move_run(rid, RunState.PAUSED, at=world.now, by="u_hr")  # HR pauses right after
            return found

        job = JobContext("j", ORG, START_RUN, "e", RUN_STARTED, MappingProxyType({"run_id": run_id}), run_id, 1)
        monkeypatch.setattr(RunStore, "get", pause_after_read)
        with tenant_scope(ORG):
            await handler(job)
        monkeypatch.setattr(RunStore, "get", original)
        return (await world.run_state(run_id))[0], executor

    status, executor = run(go())
    assert status == "paused"
    assert executor.calls == []


def test_a_task_cancelled_under_a_running_run_ends_the_run_instead_of_leaving_it_running() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.RUNNING, at=world.now)
            await world.runs.cancel_unfinished(run_id, at=world.now)  # a failure's first write, then a crash
        await world.drive(run_id, StubExecutor())
        return (await world.run_state(run_id))[0]

    assert run(go()) == "failed"


def test_a_pause_that_lands_while_the_last_task_finishes_is_not_a_job_failure() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        executor = StubExecutor()
        original = executor.execute

        async def pause_during_last(work: TaskWork) -> None:
            await original(work)
            if work.task_id == "d":
                with tenant_scope(ORG):
                    await world.runs.move_run(run_id, RunState.PAUSED, at=world.now, by="u_hr")

        executor.execute = pause_during_last  # type: ignore[method-assign]
        await world.drive(run_id, executor)  # must return, not raise
        return (await world.run_state(run_id))[0]

    assert run(go()) == "paused"


# --- reconciling with RUN-3's agents: sink rejections, deterministic failures, stale causes (ADR 0018) --------


class RejectedOnce(StubExecutor):
    """A sink refuses the first attempt of `target` (as `orchestrator.fail_task` does while the agent runs);
    the agent still reports success, which must not turn the task `done`."""

    def __init__(self, world: World, run_id: str, target: str, times: int = 1) -> None:
        super().__init__()
        self.world, self.run_id, self.target, self.times = world, run_id, target, times

    async def execute(self, work: TaskWork) -> None:
        await super().execute(work)
        if work.task_id == self.target and self.calls.count(self.target) <= self.times:
            with tenant_scope(ORG):
                failed = await self.world.runs.fail_running(
                    self.run_id,
                    self.target,
                    failure={"kind": "sink_rejected", "reason": "wrong_run"},
                    at=self.world.now,
                )
            assert failed


def test_a_sink_rejection_counts_as_an_attempt_and_the_retry_starts_clean() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, dict[str, Any]], RejectedOnce]:
        run_id = await world.start()
        executor = RejectedOnce(world, run_id, "b")
        await world.drive(run_id, executor)
        status, tasks = await world.run_state(run_id)
        return status, tasks, executor

    status, tasks, executor = run(go())
    assert status == "completed"
    assert tasks["b"]["status"] == "done", "the agent's late success never turned the rejected attempt done"
    assert tasks["b"]["attempts"] == 2, "the rejection was attempt 1"
    assert executor.calls.count("b") == 2
    assert tasks["b"]["failure"] is None, "the retry cleared the sink's stale cause"
    assert tasks["b"]["error"] is None
    moves = [(h["from"], h["to"]) for h in tasks["b"]["history"]]
    assert ("running", "failed") in moves, "the rejection shows in the task's history (and so in the stream)"


def test_a_task_rejected_on_every_attempt_blocks_the_run_and_shows_the_cause() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await world.drive(run_id, RejectedOnce(world, run_id, "b", times=99))
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "blocked"
    assert (tasks["b"]["status"], tasks["b"]["attempts"]) == ("failed", 3)
    assert tasks["b"]["failure"] == {"kind": "sink_rejected", "reason": "wrong_run"}
    assert tasks["b"]["error"] == "sink_rejected"
    assert len([e for e in world.bus.published if e["type"] == RUN_BLOCKED]) == 1


def test_a_run_whose_driver_died_after_a_sink_failed_its_task_is_recovered_by_the_next_delivery() -> None:
    """`fail_task` failed the running task, then the worker driving the run died: the run is `running`, the
    task `failed` with the sink's cause, nobody drives. The job comes back (its lease ran out) and the
    run's next delivery retries the task and finishes the run."""
    world = _world()

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await _left_running(world, run_id, "b", until=world.now - timedelta(seconds=1))
        with tenant_scope(ORG):
            await world.runs.take_over(
                run_id,
                "b",
                expected=await world.runs.task(run_id, "b") or {},
                token="tok_dead",
                lease_until=world.now + timedelta(seconds=30),
                at=world.now,
            )
            assert await world.runs.fail_running(
                run_id, "b", failure={"kind": "sink_rejected", "reason": "wrong_run"}, at=world.now
            )
        executor = StubExecutor()
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "completed"
    assert tasks["b"]["status"] == "done"
    assert tasks["b"]["failure"] is None


class Deterministic(Exception):  # noqa: N818 - a stand-in for RUN-3's AgentTaskFailedError
    kind = "grant_refused"
    retryable = False


class Passing(Exception):  # noqa: N818
    kind = "timeout"
    retryable = True


class Raising(StubExecutor):
    def __init__(self, error: BaseException, task: str = "b") -> None:
        super().__init__()
        self.error, self.task = error, task

    async def execute(self, work: TaskWork) -> None:
        await super().execute(work)
        if work.task_id == self.task:
            raise self.error


def test_an_executor_error_that_retrying_cannot_fix_blocks_the_run_at_once() -> None:
    world = _world()
    executor = Raising(Deterministic())

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "blocked"
    assert executor.calls.count("b") == 1, "no attempts wasted on a deterministic failure"
    assert (tasks["b"]["status"], tasks["b"]["attempts"], tasks["b"]["error"]) == ("failed", 1, "grant_refused")
    assert len([e for e in world.bus.published if e["type"] == RUN_BLOCKED]) == 1


def test_an_executor_error_that_may_pass_is_retried_up_to_the_maximum() -> None:
    world = _world()
    executor = Raising(Passing())

    async def go() -> tuple[str, dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await world.drive(run_id, executor)
        return await world.run_state(run_id)

    status, tasks = run(go())
    assert status == "blocked"
    assert executor.calls.count("b") == 3
    assert tasks["b"]["error"] == "timeout"


def test_a_database_error_from_the_executor_is_not_the_tasks_failure() -> None:
    """The agent executor re-raises database errors for the bus to retry: the task keeps its attempt count,
    gives its lease back, and the job is retried (not failed, not blocked)."""
    from pymongo.errors import AutoReconnect

    world = _world()
    executor = Raising(AutoReconnect("primary stepped down"))

    async def go() -> tuple[str, dict[str, dict[str, Any]], BaseException]:
        run_id = await world.start()
        try:
            await world.drive(run_id, executor)
        except BaseException as error:
            caught = error
        status, tasks = await world.run_state(run_id)
        return status, tasks, caught

    status, tasks, caught = run(go())
    assert isinstance(caught, TaskStepError)  # the graph's type-only stand-in, marked transient: the bus retries
    assert (caught.type_name, caught.transient) == ("AutoReconnect", True)
    assert status == "running"
    assert tasks["b"]["status"] == "running"
    assert tasks["b"]["attempts"] == 0, "the attempt is handed back"
    assert tasks["b"]["lease_token"] is None, "the lease is released: the retry takes the task at once"
