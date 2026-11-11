"""`RunStore` and `RunStarter` over the real `DataStore`, with an in-memory driver (RUN-1).

The driver stand-in (`onb_api.testing_mongo`, test-only) keeps tenant scoping, unique indexes and
upserts real; the event bus is a recording stand-in that runs `change` like the outbox does. The real
bus, transactions and replica set are proved in `apps/orchestrator/tests_int`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_agents.dag import CompileError, CompileReason, compile_dag
from onb_agents.runs import (
    RUN_STARTED,
    RUN_TASKS,
    RUNS,
    START_RUN,
    SUBSCRIPTIONS,
    IllegalTransitionError,
    RunStarter,
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    run_bus,
    run_records,
    started_key,
)
from onb_agents.runs.testing import OutboxStandIn, diamond_template, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_data import Actor, AuditOutcome, AuditQuery, NoTenantContextError, tenant_scope
from onb_data.testing import InMemoryAuditLog

NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
LATER = datetime(2026, 11, 11, 9, 31, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _store(db: MemoryDatabase | None = None) -> RunStore:
    return RunStore(memory_store(db or MemoryDatabase()))


async def _insert(store: RunStore, role: str = "sales", run_id: str = "run_1") -> None:
    dag = compile_dag(diamond_template(), hire_variables_for(role))
    records = run_records(
        org_id="org_a", run_id=run_id, hire_id="h_1", dag=dag, actor_id="u_hr", traceparent=None, now=NOW
    )
    await store.insert(records)


# --- the store ------------------------------------------------------------------------------------


def test_the_store_writes_the_run_and_its_tasks_in_the_callers_org() -> None:
    db = MemoryDatabase()
    store = _store(db)

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            assert (await store.get("run_1") or {})["template_version"] == 1
            tasks = await store.tasks("run_1")
            assert [t["task_id"] for t in tasks] == ["a", "b", "c", "d", "e", "f"]
        with tenant_scope("org_b"):
            assert await store.get("run_1") is None  # another org's run is not there
            assert await store.tasks("run_1") == []

    run(go())
    assert {d["org_id"] for d in db.raw(RUNS)} == {"org_a"}
    assert {d["org_id"] for d in db.raw(RUN_TASKS)} == {"org_a"}


def test_the_store_needs_a_tenant_context() -> None:
    with pytest.raises(NoTenantContextError):
        run(_store().get("run_1"))


def test_indexes_start_with_org_id_and_task_ids_are_unique_per_run() -> None:
    db = MemoryDatabase()
    run(_store(db).ensure_indexes())
    keys = {(name, index.keys, index.unique) for name, coll in db.collections.items() for index in coll.indexes}
    assert (RUN_TASKS, ("org_id", "run_id", "task_id"), True) in keys
    assert (RUNS, ("org_id", "hire_id"), False) in keys
    assert all(k[1][0] == "org_id" for k in keys)


def test_a_task_moves_only_along_the_state_machine_and_stamps_its_times() -> None:
    store = _store()

    async def go() -> list[dict[str, Any]]:
        with tenant_scope("org_a"):
            await _insert(store)
            await store.move_task("run_1", "a", TaskState.READY, at=NOW)
            await store.move_task("run_1", "a", TaskState.RUNNING, at=NOW)
            moved = await store.move_task("run_1", "a", TaskState.DONE, at=LATER)
            assert moved["status"] == "done"
            with pytest.raises(IllegalTransitionError):
                await store.move_task("run_1", "a", TaskState.RUNNING, at=LATER)  # done never runs again
            with pytest.raises(IllegalTransitionError):
                await store.move_task("run_1", "b", TaskState.RUNNING, at=LATER)  # pending → running
            with pytest.raises(IllegalTransitionError):
                await store.move_task("run_1", "e", TaskState.READY, at=LATER)  # skipped stays skipped
            return await store.tasks("run_1")

    tasks = {t["task_id"]: t for t in run(go())}
    a = tasks["a"]
    assert (a["status"], a["ready_at"], a["started_at"], a["finished_at"], a["attempts"]) == (
        "done",
        NOW,
        NOW,
        LATER,
        1,
    )
    assert tasks["b"]["status"] == "pending"
    assert tasks["b"]["started_at"] is None
    assert tasks["e"]["status"] == "skipped"


def test_a_move_on_an_unknown_task_is_not_found() -> None:
    store = _store()

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            with pytest.raises(LookupError):
                await store.move_task("run_1", "ghost", TaskState.READY, at=NOW)
        # another org's task is the same as no task
        with tenant_scope("org_b"), pytest.raises(LookupError):
            await store.move_task("run_1", "a", TaskState.READY, at=NOW)

    run(go())


def test_a_move_that_loses_a_race_is_a_conflict_and_writes_nothing() -> None:
    store = _store()

    async def go() -> str:
        with tenant_scope("org_a"):
            await _insert(store)
            stale = await store.task("run_1", "a")
            await store.move_task("run_1", "a", TaskState.READY, at=NOW)
            with pytest.raises(TransitionConflictError):
                await store.move_task("run_1", "a", TaskState.READY, at=LATER, expected=stale)
            return str((await store.task("run_1", "a") or {})["status"])

    assert run(go()) == "ready"


def test_a_run_moves_only_along_its_state_machine() -> None:
    store = _store()

    async def go() -> None:
        with tenant_scope("org_a"):
            await _insert(store)
            await store.move_run("run_1", RunState.RUNNING, at=NOW)
            with pytest.raises(IllegalTransitionError):
                await store.move_run("run_1", RunState.STARTED, at=NOW)
            await store.move_run("run_1", RunState.COMPLETED, at=LATER)
            with pytest.raises(IllegalTransitionError):
                await store.move_run("run_1", RunState.FAILED, at=LATER)
            assert (await store.get("run_1") or {})["status"] == "completed"
            with pytest.raises(LookupError):
                await store.move_run("run_ghost", RunState.RUNNING, at=NOW)

    run(go())


# --- the starter ----------------------------------------------------------------------------------


def _starter(store: RunStore, bus: OutboxStandIn, audit: InMemoryAuditLog) -> RunStarter:
    return RunStarter(store, bus, audit, clock=lambda: NOW, new_id=lambda: "run_fixed")


def test_starting_a_run_writes_it_through_the_outbox_with_a_fixed_key_and_audits_it() -> None:
    db = MemoryDatabase()
    store, bus, audit = _store(db), OutboxStandIn(), InMemoryAuditLog()

    async def go() -> None:
        with tenant_scope("org_a"):
            started = await _starter(store, bus, audit).start(
                template=diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
            )
            assert started.run["_id"] == "run_fixed"
            assert started.run["thread_id"] == "org_a:run_fixed"
            assert [t["status"] for t in started.tasks] == ["pending"] * 6
            entries = (await audit.list(AuditQuery(), page=1, limit=10)).entries
            assert [(e.action, e.target, e.outcome) for e in entries] == [
                ("run.start", "run:run_fixed", AuditOutcome.SUCCESS)
            ]
            assert entries[0].actor == Actor.user("u_hr")

    run(go())
    assert bus.published == [
        {
            "org_id": "org_a",
            "type": RUN_STARTED,
            "payload": {"run_id": "run_fixed"},
            "idempotency_key": "run:run_fixed:started",
            "run_id": "run_fixed",
        }
    ]
    assert len(db.raw(RUNS)) == 1
    assert len(db.raw(RUN_TASKS)) == 6


def test_a_template_that_does_not_compile_starts_nothing() -> None:
    db = MemoryDatabase()
    store, bus, audit = _store(db), OutboxStandIn(), InMemoryAuditLog()
    archived = diamond_template().model_copy(update={"status": "archived"})

    async def go() -> None:
        with tenant_scope("org_a"):
            with pytest.raises(CompileError) as raised:
                await _starter(store, bus, audit).start(
                    template=archived, hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
                )
            assert raised.value.reason is CompileReason.NOT_PUBLISHED
            with pytest.raises(CompileError) as missing:
                await _starter(store, bus, audit).start(
                    template=diamond_template(), hire_id="h_1", variables={"role": "sales"}, actor_id="u_hr"
                )
            assert missing.value.reason is CompileReason.MISSING_VARIABLE

    run(go())
    assert bus.published == []
    assert db.raw(RUNS) == []
    assert db.raw(RUN_TASKS) == []


def test_the_traceparent_of_the_request_is_stored_on_the_run() -> None:
    store, bus, audit = _store(), OutboxStandIn(), InMemoryAuditLog()
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    starter = RunStarter(store, bus, audit, clock=lambda: NOW, new_id=lambda: "run_t", traceparent=lambda: traceparent)

    async def go() -> object:
        with tenant_scope("org_a"):
            started = await starter.start(
                template=diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
            )
            return started.run["traceparent"]

    assert run(go()) == traceparent


def test_the_run_bus_subscribes_the_orchestrator_to_run_started() -> None:
    assert SUBSCRIPTIONS == {RUN_STARTED: (START_RUN,)}
    assert started_key("run_x") == "run:run_x:started"
    bus = run_bus(memory_store(MemoryDatabase()))
    assert bus.consumers() == (START_RUN,)
