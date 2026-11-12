# ruff: noqa: S105, S106, S107  (lease tokens in these tests are labels, not secrets)
"""The run store's control surface (RUN-2): lease and takeover, history, listing, run moves.

Same in-memory driver as `test_run_store.py`; the real database is proved in `apps/orchestrator/tests_int`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from onb_agents.dag import compile_dag
from onb_agents.runs import (
    IllegalTransitionError,
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    lease_expired,
    run_records,
)
from onb_agents.runs.testing import diamond_template, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_data import tenant_scope

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


async def _insert(store: RunStore, run_id: str = "run_1", hire_id: str = "h_1") -> None:
    dag = compile_dag(diamond_template(), hire_variables_for("sales"))
    records = run_records(
        org_id="org_a", run_id=run_id, hire_id=hire_id, dag=dag, actor_id="u_hr", traceparent=None, now=T0
    )
    await store.insert(records)


async def _running(store: RunStore, task_id: str = "a", token: str = "tok_1", until: float = 30) -> dict[str, Any]:
    ready = await store.move_task("run_1", task_id, TaskState.READY, at=at(1))
    return await store.move_task(
        "run_1",
        task_id,
        TaskState.RUNNING,
        at=at(2),
        expected=ready,
        fields={"lease_token": token, "lease_until": at(until)},
    )


def test_every_move_is_recorded_in_the_tasks_history_in_order() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await _running(store)
            await store.move_task("run_1", "a", TaskState.DONE, at=at(3), by="u_hr")
            task = await store.task("run_1", "a") or {}
            assert [(h["from"], h["to"]) for h in task["history"]] == [
                ("pending", "ready"),
                ("ready", "running"),
                ("running", "done"),
            ]
            assert task["history"][-1]["by"] == "u_hr"
            assert task["history"][-1]["at"] == at(3)

    run(go())


def test_leaving_running_clears_the_lease() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await _running(store)
            done = await store.move_task("run_1", "a", TaskState.DONE, at=at(3))
            assert done["lease_token"] is None
            assert done["lease_until"] is None
            assert ((await store.task("run_1", "a")) or {})["lease_token"] is None

    run(go())


def test_a_failed_task_can_be_retried_through_ready_and_attempts_can_be_reset() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await _running(store)
            await store.move_task("run_1", "a", TaskState.FAILED, at=at(3), fields={"error": "StubTaskError"})
            again = await store.move_task("run_1", "a", TaskState.READY, at=at(4), fields={"attempts": 0})
            assert (again["status"], again["attempts"]) == ("ready", 0)
            with pytest.raises(IllegalTransitionError):
                await store.move_task("run_1", "a", TaskState.RUNNING, at=at(5), expected={**again, "status": "failed"})

    run(go())


def test_the_lease_expires_when_its_time_passes_or_it_was_never_set() -> None:
    assert lease_expired({"lease_until": at(10)}, at(11))
    assert lease_expired({"lease_until": at(10)}, at(10))
    assert not lease_expired({"lease_until": at(10)}, at(9))
    assert lease_expired({"lease_until": None}, at(0))
    assert lease_expired({}, at(0))


def test_a_task_whose_lease_ran_out_is_taken_over_once() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            running = await _running(store, token="tok_dead", until=30)
            taken = await store.take_over(
                "run_1", "a", expected=running, token="tok_new", lease_until=at(100), at=at(60)
            )
            assert taken["lease_token"] == "tok_new"
            assert taken["attempts"] == running["attempts"] + 1
            assert taken["history"][-1]["note"] == "takeover"
            with pytest.raises(TransitionConflictError):  # the second taker loses the race
                await store.take_over("run_1", "a", expected=running, token="tok_3", lease_until=at(100), at=at(61))
            stored = await store.task("run_1", "a") or {}
            assert stored["lease_token"] == "tok_new"

    run(go())


def test_only_the_holder_renews_or_releases_a_lease() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await _running(store, token="tok_1")
            assert not await store.renew_lease("run_1", "a", "tok_other", at(90))
            assert await store.renew_lease("run_1", "a", "tok_1", at(90))
            assert ((await store.task("run_1", "a")) or {})["lease_until"] == at(90)
            assert not await store.release_lease("run_1", "a", "tok_other")
            assert await store.release_lease("run_1", "a", "tok_1")
            assert lease_expired((await store.task("run_1", "a")) or {}, at(3))
            assert not await store.renew_lease("run_1", "a", "tok_1", at(90))  # released: no longer held

    run(go())


def test_a_finished_task_cannot_be_renewed_or_taken_over() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            running = await _running(store)
            await store.move_task("run_1", "a", TaskState.DONE, at=at(3))
            assert not await store.renew_lease("run_1", "a", "tok_1", at(90))
            with pytest.raises(TransitionConflictError):
                await store.take_over("run_1", "a", expected=running, token="t", lease_until=at(99), at=at(60))

    run(go())


def test_a_run_move_records_its_history_and_who_did_it() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await store.move_run("run_1", RunState.RUNNING, at=at(1))
            paused = await store.move_run("run_1", RunState.PAUSED, at=at(2), by="u_hr")
            assert [(h["from"], h["to"]) for h in paused["history"]] == [("started", "running"), ("running", "paused")]
            assert paused["history"][-1]["by"] == "u_hr"
            with pytest.raises(IllegalTransitionError):
                await store.move_run("run_1", RunState.PAUSED, at=at(3), by="u_hr")  # already paused

    run(go())


def test_a_run_move_can_be_required_to_start_from_a_given_state() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            with pytest.raises(TransitionConflictError):
                await store.move_run("run_1", RunState.RUNNING, at=at(1), expected_status=RunState.BLOCKED)
            moved = await store.move_run("run_1", RunState.RUNNING, at=at(1), expected_status=RunState.STARTED)
            assert moved["status"] == "running"

    run(go())


def test_runs_are_listed_newest_first_filtered_and_counted_in_the_callers_org_only() -> None:
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store, "run_1", "h_1")
            await _insert(store, "run_2", "h_2")
            await _insert(store, "run_3", "h_2")
            await store.move_run("run_3", RunState.RUNNING, at=at(5))
            everything, total = await store.list_runs()
            assert total == 3
            assert {r["_id"] for r in everything} == {"run_1", "run_2", "run_3"}
            mine, total = await store.list_runs(hire_ids=["h_2"])
            assert {r["_id"] for r in mine} == {"run_2", "run_3"}
            assert total == 2
            none, total = await store.list_runs(hire_ids=[])
            assert none == []
            assert total == 0
            running, total = await store.list_runs(status=RunState.RUNNING)
            assert [r["_id"] for r in running] == ["run_3"]
            assert total == 1
            page, total = await store.list_runs(skip=2, limit=2)
            assert len(page) == 1
            assert total == 3
        with tenant_scope("org_b"):
            assert await store.list_runs() == ([], 0)

    run(go())


def test_a_document_of_another_shape_in_the_collection_is_not_listed() -> None:
    """The eval harness's seed keeps runs of its own shape (`status: in_progress`) in the same collection."""
    db = MemoryDatabase()
    store = RunStore(memory_store(db))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store, "run_1", "h_1")
            await (
                memory_store(db)
                .tenant("onboarding_runs")
                .insert_one(
                    {"_id": "run_legacy", "hire_id": "h_1", "status": "blocked"}  # a valid status, but no DAG
                )
            )
            found, total = await store.list_runs()
            assert [r["_id"] for r in found] == ["run_1"]
            assert total == 1

    run(go())


def test_a_worker_that_was_taken_over_cannot_finish_the_task_the_new_holder_runs() -> None:
    """A running task is moved only by the holder of its lease: the old holder's `done` conflicts."""
    store = RunStore(memory_store(MemoryDatabase()))

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            old_holder = await _running(store, token="tok_old", until=30)
            await store.take_over("run_1", "a", expected=old_holder, token="tok_new", lease_until=at(100), at=at(60))
            with pytest.raises(TransitionConflictError):
                await store.move_task("run_1", "a", TaskState.DONE, at=at(61), expected=old_holder)
            assert ((await store.task("run_1", "a")) or {})["status"] == "running"

    run(go())
