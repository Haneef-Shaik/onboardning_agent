"""How a run ends when something goes wrong (RUN-1 review fixes; PRD FR-R2, §7.2 at-least-once).

- A failed task cancels every task that has not finished, including a sibling LangGraph cut off
  mid-step, so no task of a failed run is left `running` or `pending`.
- A deterministic error (a blocked task, a broken graph) fails the run at once instead of being
  retried until it is dead-lettered with the run stuck `running`.
- A transient error (the database, a compare-and-set race) is raised so the bus retries the job,
  and the run is left to the retry.
- Overlapping deliveries of one run never execute a task twice, and a run already finished by the
  other delivery is not an error.
- A node's error reaches the checkpoint (LangGraph stores it as a pending write) as its type only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.runs import RUN_STARTED, START_RUN, RunStarter, RunStore, TaskState
from onb_agents.runs.testing import OutboxStandIn, diamond_template, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext
from onb_orchestrator.checkpointer import CHECKPOINT_WRITES
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.runner import RunExecutor, TaskStepError
from pymongo.errors import AutoReconnect

ORG = "org_a"
UNFINISHED = {"pending", "ready", "running", "waiting_approval"}


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _job(run_id: str, attempt: int = 1) -> JobContext:
    return JobContext(
        "job_1", ORG, START_RUN, "ev_1", RUN_STARTED, MappingProxyType({"run_id": run_id}), run_id, attempt
    )


async def _start(store: DataStore) -> str:
    starter = RunStarter(RunStore(store), OutboxStandIn(), InMemoryAuditLog())
    with tenant_scope(ORG):
        started = await starter.start(
            template=diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
        )
    return str(started.run["_id"])


async def _state(store: DataStore, run_id: str) -> tuple[str, dict[str, str]]:
    runs = RunStore(store)
    with tenant_scope(ORG):
        found = await runs.get(run_id)
        tasks = {t["task_id"]: t["status"] for t in await runs.tasks(run_id)}
    assert found is not None
    return str(found["status"]), tasks


def test_a_failed_task_cancels_its_cut_off_sibling_and_every_task_after_it() -> None:
    store = memory_store(MemoryDatabase())
    executor = StubExecutor(delays={"c": 1.0}, fail=frozenset({"b"}))

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await _start(store)
        with tenant_scope(ORG):
            await start_run_handler(store, executor)(_job(run_id))
        return await _state(store, run_id)

    status, tasks = run(go())
    assert status == "failed"
    assert tasks["b"] == "failed"
    assert tasks["c"] == "cancelled", "the sibling LangGraph cut off is left running"
    assert tasks["d"] == "cancelled"
    assert tasks["f"] in {"done", "cancelled"}
    assert not set(tasks.values()) & UNFINISHED, tasks


def test_a_blocked_task_fails_the_run_instead_of_retrying_forever() -> None:
    store = memory_store(MemoryDatabase())

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await _start(store)
        with tenant_scope(ORG):
            await store.tenant("run_tasks").update_one(
                {"run_id": run_id, "task_id": "a"}, {"$set": {"status": "waiting_approval"}}
            )
            await start_run_handler(store, StubExecutor())(_job(run_id))  # returns: the job is done
        return await _state(store, run_id)

    status, tasks = run(go())
    assert status == "failed"
    assert not set(tasks.values()) & UNFINISHED, tasks


def test_a_database_error_is_retried_and_the_run_is_left_to_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    store = memory_store(MemoryDatabase())

    async def flaky(self: RunExecutor, run_id: str, task_id: str, *, traceparent: str | None) -> None:
        raise AutoReconnect("primary stepped down")

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await _start(store)
        with monkeypatch.context() as patched:
            patched.setattr(RunExecutor, "run_task", flaky)
            with tenant_scope(ORG), pytest.raises(TaskStepError) as raised:
                await start_run_handler(store, StubExecutor())(_job(run_id))
        assert raised.value.transient
        assert "stepped down" not in str(raised.value)  # the type only
        first = await _state(store, run_id)
        with tenant_scope(ORG):
            await start_run_handler(store, StubExecutor())(_job(run_id, attempt=2))  # the retry finishes it
        assert first[0] == "running"
        return await _state(store, run_id)

    status, tasks = run(go())
    assert status == "completed"
    assert set(tasks.values()) <= {"done", "skipped"}


def test_overlapping_deliveries_never_run_a_task_twice_and_a_finished_run_is_not_an_error() -> None:
    store = memory_store(MemoryDatabase())
    executor = StubExecutor(delay=0.05)

    async def go() -> tuple[list[object], tuple[str, dict[str, str]]]:
        run_id = await _start(store)
        handler = start_run_handler(store, executor)

        async def deliver(attempt: int) -> None:
            with tenant_scope(ORG):
                await handler(_job(run_id, attempt))

        outcomes = await asyncio.gather(deliver(1), deliver(2), return_exceptions=True)
        await deliver(3)  # whatever the overlap left, a redelivery finishes the run without error
        return list(outcomes), await _state(store, run_id)

    outcomes, (status, tasks) = run(go())
    assert all(o is None or (isinstance(o, TaskStepError) and o.transient) for o in outcomes), outcomes
    assert status == "completed"
    assert set(tasks.values()) <= {"done", "skipped"}
    assert sorted(executor.calls) == sorted(set(executor.calls)), "a task executed twice"


def test_a_task_another_delivery_is_running_is_not_executed_again() -> None:
    store = memory_store(MemoryDatabase())
    executor = StubExecutor()

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await _start(store)
        runs = RunStore(store)
        at = datetime(2026, 11, 12, tzinfo=UTC)
        with tenant_scope(ORG):
            await runs.move_task(run_id, "a", TaskState.READY, at=at)
            await runs.move_task(run_id, "a", TaskState.RUNNING, at=at)  # another delivery holds it
            with pytest.raises(TaskStepError) as raised:
                await start_run_handler(store, executor)(_job(run_id))
        assert raised.value.transient
        assert raised.value.type_name == "TaskInProgressError"
        return await _state(store, run_id)

    status, tasks = run(go())
    assert executor.calls == []
    assert (status, tasks["a"]) == ("running", "running")  # left for the delivery running it


def test_a_node_error_reaches_the_checkpoint_as_its_type_only() -> None:
    db = MemoryDatabase()
    store = memory_store(db)

    async def go() -> None:
        run_id = await _start(store)
        with tenant_scope(ORG):
            await store.tenant("run_tasks").update_one(
                {"run_id": run_id, "task_id": "a"}, {"$set": {"depends_on": ["ghost-value-xyz"]}}
            )
            await start_run_handler(store, StubExecutor())(_job(run_id))

    run(go())
    stored = repr(db.raw(CHECKPOINT_WRITES))
    assert "TaskBlockedError" in stored
    assert "dependencies" not in stored  # the message never reaches the checkpoint


def test_cancelling_an_unfinished_task_is_stamped_and_finished_ones_keep_their_state() -> None:
    store = memory_store(MemoryDatabase())
    at = datetime(2026, 11, 12, tzinfo=UTC)

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await _start(store)
        runs = RunStore(store)
        with tenant_scope(ORG):
            await runs.move_task(run_id, "a", TaskState.READY, at=at)
            await runs.move_task(run_id, "a", TaskState.RUNNING, at=at)
            await runs.move_task(run_id, "a", TaskState.DONE, at=at)
            cancelled = await runs.cancel_unfinished(run_id, at=at)
            assert sorted(cancelled) == ["b", "c", "d", "e", "f"]
            return {t["task_id"]: t for t in await runs.tasks(run_id)}

    tasks = run(go())
    assert tasks["a"]["status"] == "done"
    assert {tasks[t]["status"] for t in "bcdef"} == {"cancelled"}
    assert tasks["b"]["finished_at"] == at
