"""Executing a run as a LangGraph `StateGraph` (RUN-1 boxes 1-4; PRD FR-R2, FR-R3, §7.1, §7.9).

A run is started with the real `RunStarter` (the outbox is a recording stand-in that runs `change`),
then the `orchestrator.start_run` handler executes it over the real `DataStore` with an in-memory
driver. The same flow through the real bus on the compose replica set is in `tests_int/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.dag import compile_dag
from onb_agents.runs import (
    RUN_STARTED,
    START_RUN,
    RunStarter,
    RunStore,
    TaskState,
    thread_id,
)
from onb_agents.runs.testing import OutboxStandIn, diamond_template, hire_variables_for, uneven_diamond_template
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.templates import Template
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext
from onb_orchestrator.checkpointer import CHECKPOINT_WRITES, CHECKPOINTS
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor, TaskWork
from onb_orchestrator.graph import build_run_graph
from onb_orchestrator.runner import RunExecutor

ORG = "org_a"


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class World:
    db: MemoryDatabase
    store: DataStore
    runs: RunStore
    bus: OutboxStandIn
    executor: StubExecutor

    async def start(self, template: Template | None = None, role: str = "engineer", org: str = ORG) -> str:
        starter = RunStarter(self.runs, self.bus, InMemoryAuditLog())
        with tenant_scope(org):
            started = await starter.start(
                template=template or diamond_template(),
                hire_id="h_1",
                variables=hire_variables_for(role),
                actor_id="u_hr",
            )
        return str(started.run["_id"])

    async def execute(self, run_id: str, org: str = ORG, attempt: int = 1) -> None:
        handler = start_run_handler(self.store, self.executor)
        job = JobContext(
            job_id="job_1",
            org_id=org,
            consumer=START_RUN,
            event_id="ev_1",
            event_type=RUN_STARTED,
            payload=MappingProxyType({"run_id": run_id}),
            run_id=run_id,
            attempt=attempt,
        )
        with tenant_scope(org):  # the worker sets the job's tenant context before the handler
            await handler(job)

    async def tasks(self, run_id: str, org: str = ORG) -> dict[str, dict[str, Any]]:
        with tenant_scope(org):
            return {t["task_id"]: t for t in await self.runs.tasks(run_id)}

    async def get(self, run_id: str, org: str = ORG) -> dict[str, Any]:
        with tenant_scope(org):
            found = await self.runs.get(run_id)
        assert found is not None
        return found


def _world(delay: float = 0.0, fail: frozenset[str] = frozenset()) -> World:
    db = MemoryDatabase()
    store = memory_store(db)
    return World(db, store, RunStore(store), OutboxStandIn(), StubExecutor(delay=delay, fail=fail))


def _overlap(x: Mapping[str, Any], y: Mapping[str, Any]) -> bool:
    return bool(x["started_at"] < y["finished_at"] and y["started_at"] < x["finished_at"])


# --- box 1: a diamond runs B and C in parallel, and D only after both ----------------------------


def test_in_a_diamond_b_and_c_run_in_parallel_and_d_starts_only_after_both() -> None:
    world = _world(delay=0.15)

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await world.start()
        await world.execute(run_id)
        return await world.tasks(run_id)

    tasks = run(go())
    a, b, c, d = (tasks[t] for t in "abcd")
    assert all(t["status"] == "done" for t in (a, b, c, d))
    assert _overlap(b, c), f"B {b['started_at']}-{b['finished_at']} and C {c['started_at']}-{c['finished_at']}"
    assert b["started_at"] >= a["finished_at"]
    assert c["started_at"] >= a["finished_at"]
    assert d["started_at"] >= max(b["finished_at"], c["finished_at"])
    calls = world.executor.calls
    assert calls.index("a") < calls.index("b")
    assert calls.index("d") > max(calls.index("b"), calls.index("c"))


def test_the_join_waits_for_the_slower_branch() -> None:
    """`d` depends on `b` and `c`; `c` is slow. `d` must not start when only `b` has finished."""
    world = _world()
    world.executor.delays = {"c": 0.3, "b": 0.0}

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await world.start()
        await world.execute(run_id)
        return await world.tasks(run_id)

    tasks = run(go())
    assert tasks["d"]["started_at"] >= tasks["c"]["finished_at"]
    assert world.executor.calls.count("d") == 1


def test_a_join_waits_for_every_branch_even_a_longer_one() -> None:
    """`d` joins `b2` (two steps after `a`) and `c` (one step): it starts only after both finished,
    and the run never asks the runner to ready `d` early."""
    world = _world()

    async def go() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        run_id = await world.start(template=uneven_diamond_template())
        await world.execute(run_id)
        return await world.get(run_id), await world.tasks(run_id)

    run_doc, tasks = run(go())
    assert run_doc["status"] == "completed"
    assert tasks["d"]["started_at"] >= max(tasks["b2"]["finished_at"], tasks["c"]["finished_at"])
    assert world.executor.calls.count("d") == 1
    assert world.executor.calls[-1] == "d"


def test_the_run_completes_and_every_task_ends_done_or_skipped() -> None:
    world = _world()

    async def go() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        run_id = await world.start(role="sales")
        await world.execute(run_id)
        return await world.get(run_id), await world.tasks(run_id)

    run_doc, tasks = run(go())
    assert run_doc["status"] == "completed"
    assert {t: tasks[t]["status"] for t in tasks} == {
        "a": "done",
        "b": "done",
        "c": "done",
        "d": "done",
        "e": "skipped",
        "f": "done",
    }


# --- box 2: skipped dependencies don't block; illegal transitions are rejected --------------------


def test_a_skipped_dependency_does_not_block_its_dependent() -> None:
    world = _world()

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await world.start(role="sales")  # `e` is skipped; `f` depends on it
        await world.execute(run_id)
        return await world.tasks(run_id)

    tasks = run(go())
    assert tasks["e"]["status"] == "skipped"
    assert tasks["e"]["started_at"] is None
    assert tasks["f"]["status"] == "done"
    assert "e" not in world.executor.calls  # a skipped task never executes
    assert "f" in world.executor.calls


def test_a_task_whose_dependency_is_not_finished_is_refused() -> None:
    """The runner re-checks FR-R3 itself: even if asked to, it never readies a blocked task."""
    world = _world()

    async def go() -> None:
        run_id = await world.start()
        runner = RunExecutor(world.store, world.executor)
        with tenant_scope(ORG):
            with pytest.raises(RuntimeError, match="dependencies"):
                await runner.run_task(run_id, "d", traceparent=None)
            assert (await world.runs.task(run_id, "d") or {})["status"] == "pending"

    run(go())
    assert world.executor.calls == []


def test_an_illegal_transition_is_rejected_and_nothing_is_written() -> None:
    from onb_agents.runs import IllegalTransitionError

    world = _world()

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await world.start()
        with tenant_scope(ORG):
            with pytest.raises(IllegalTransitionError):
                await world.runs.move_task(run_id, "a", TaskState.RUNNING, at=datetime.now().astimezone())
            with pytest.raises(IllegalTransitionError):
                await world.runs.move_task(run_id, "a", TaskState.DONE, at=datetime.now().astimezone())
        return await world.tasks(run_id)

    assert run(go())["a"]["status"] == "pending"


def test_a_failing_task_fails_the_run_and_its_dependents_never_start() -> None:
    world = _world(fail=frozenset({"b"}))

    async def go() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        run_id = await world.start()
        await world.execute(run_id)
        return await world.get(run_id), await world.tasks(run_id)

    run_doc, tasks = run(go())
    assert run_doc["status"] == "failed"
    assert tasks["b"]["status"] == "failed"
    assert tasks["d"]["status"] == "cancelled"  # a failed run leaves no task unfinished
    assert tasks["d"]["started_at"] is None
    assert "d" not in world.executor.calls


# --- box 3: the checkpoint thread id is org_id:run_id --------------------------------------------


def test_the_checkpoint_thread_id_is_org_id_colon_run_id() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        await world.execute(run_id)
        return run_id

    run_id = run(go())
    checkpoints = world.db.raw(CHECKPOINTS)
    assert checkpoints, "the graph wrote no checkpoint"
    assert {d["thread_id"] for d in checkpoints} == {f"{ORG}:{run_id}"} == {thread_id(ORG, run_id)}
    assert {d["thread_id"] for d in world.db.raw(CHECKPOINT_WRITES)} == {f"{ORG}:{run_id}"}
    assert {d["org_id"] for d in checkpoints + world.db.raw(CHECKPOINT_WRITES)} == {ORG}


def test_a_graph_built_for_a_run_is_configured_with_its_thread() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        with tenant_scope(ORG):
            built = build_run_graph(await world.get(run_id), RunExecutor(world.store, world.executor))
        return str(built.config["configurable"]["thread_id"])

    assert run(go()).startswith(f"{ORG}:run_")


# --- box 4: a run stays pinned to its version ------------------------------------------------------


def test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published() -> None:
    world = _world()
    v1 = diamond_template(version=1)
    v2 = diamond_template(version=2, extra_task=True)  # the newer version adds `g`

    async def go() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
        run_id = await world.start(template=v1)
        newer = await world.start(template=v2)  # published (and used) after the first run started
        await world.execute(run_id)
        return await world.get(run_id), await world.tasks(run_id), await world.get(newer)

    pinned, tasks, newer = run(go())
    assert pinned["template_version"] == 1
    assert pinned["dag_hash"] == compile_dag(v1, hire_variables_for()).hash
    assert sorted(tasks) == ["a", "b", "c", "d", "e", "f"]  # no `g`
    assert "g" not in world.executor.calls
    assert newer["template_version"] == 2
    assert newer["dag_hash"] != pinned["dag_hash"]


def test_a_run_whose_stored_dag_was_changed_is_not_executed() -> None:
    world = _world()

    async def go() -> dict[str, Any]:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.store.tenant("onboarding_runs").update_by_id(run_id, {"$set": {"dag.order": ["a"]}})
        await world.execute(run_id)
        return await world.get(run_id)

    assert run(go())["status"] == "failed"
    assert world.executor.calls == []


# --- the handler is idempotent and tenant-bound ---------------------------------------------------


def test_running_the_handler_again_executes_nothing_twice() -> None:
    world = _world()

    async def go() -> dict[str, Any]:
        run_id = await world.start()
        await world.execute(run_id)
        await world.execute(run_id, attempt=2)  # at-least-once delivery
        return await world.get(run_id)

    assert run(go())["status"] == "completed"
    assert sorted(world.executor.calls) == ["a", "b", "c", "d", "e", "f"]


def test_a_redelivered_job_resumes_a_run_that_stopped_part_way() -> None:
    world = _world()

    async def go() -> dict[str, dict[str, Any]]:
        run_id = await world.start()
        runner = RunExecutor(world.store, world.executor)
        with tenant_scope(ORG):
            await runner.run_task(run_id, "a", traceparent=None)  # a crashed attempt finished `a` only
            await world.runs.move_task(run_id, "b", TaskState.READY, at=datetime.now().astimezone())
        await world.execute(run_id, attempt=2)
        return await world.tasks(run_id)

    tasks = run(go())
    assert all(t["status"] in {"done", "skipped"} for t in tasks.values())
    assert world.executor.calls.count("a") == 1


def test_a_job_for_a_run_the_org_does_not_have_does_nothing() -> None:
    world = _world()

    async def go() -> None:
        run_id = await world.start(org="org_b")
        await world.execute(run_id, org=ORG)  # org A's job naming org B's run
        await world.execute("run_missing", org=ORG)
        assert (await world.get(run_id, org="org_b"))["status"] == "started"

    run(go())
    assert world.executor.calls == []
    assert world.db.raw(CHECKPOINTS) == []


@pytest.mark.parametrize("payload", [{}, {"run_id": 7}, {"run_id": ""}, {"run_id": "x" * 200}])
def test_a_job_with_a_bad_payload_is_refused(payload: dict[str, Any]) -> None:
    world = _world()
    handler = start_run_handler(world.store, world.executor)
    job = JobContext("j", ORG, START_RUN, "e", RUN_STARTED, MappingProxyType(payload), None, 1)

    async def go() -> None:
        with tenant_scope(ORG), pytest.raises(ValueError, match="run_id"):
            await handler(job)

    run(go())


def test_the_stub_executor_records_the_work_it_was_given() -> None:
    executor = StubExecutor()
    work = TaskWork(run_id="r", task_id="t", agent="db_agent", action="x", type="action", attempt=1)
    run(executor.execute(work))
    assert executor.calls == ["t"]
    assert executor.work == [work]


@pytest.mark.parametrize("status", ["in_progress", "blocked", "paused"])
def test_a_run_in_a_state_this_orchestrator_does_not_drive_is_left_alone(status: str) -> None:
    """Runs RUN-1 does not own (seeded ones, later goals' states) are never executed or failed by it."""
    world = _world()

    async def go() -> dict[str, Any]:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.store.tenant("onboarding_runs").update_by_id(run_id, {"$set": {"status": status}})
        await world.execute(run_id)
        return await world.get(run_id)

    assert run(go())["status"] == status
    assert world.executor.calls == []
