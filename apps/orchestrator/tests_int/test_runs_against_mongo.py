"""RUN-1 against the compose Mongo replica set: a run started through the real outbox is claimed by a
real worker and executed as a LangGraph graph with the tenant checkpointer (PRD FR-R1-R3, §7.1, §7.9).

Every test uses its own scratch database and the T-2 safety net: any command on a tenant collection
without `org_id` fails the test. Root read-backs go through `scratch.find*` (declared to the net).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from onb_agents.dag import compile_dag
from onb_agents.runs import START_RUN, RunStarter, RunStore, run_bus
from onb_agents.runs.testing import diamond_template, hire_variables_for
from onb_contracts.templates import Template
from onb_data import Actor, AuditLog, AuditOutcome, DataStore, connect, tenant_scope
from onb_eventbus import MongoEventBus, QueueSettings
from onb_orchestrator.checkpointer import TenantCheckpointSaver
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.policy import RunPolicy

from .conftest import Scratch

ORG_A = "org_acme"
ORG_B = "org_globex"


class FailingAudit:
    """An audit log whose write fails: the action must then not happen at all (AUD-1)."""

    async def audit(self, **_: Any) -> str:
        raise RuntimeError("the audit write failed")


@dataclass
class Stack:
    store: DataStore
    bus: MongoEventBus
    executor: StubExecutor
    policy: RunPolicy | None = None

    async def start(self, template: Template | None = None, org: str = ORG_A, auditor: Any = None) -> dict[str, Any]:
        starter = RunStarter(RunStore(self.store), self.bus, auditor or AuditLog(self.store))
        with tenant_scope(org):
            started = await starter.start(
                template=template or diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
            )
        return started.run

    async def drain(self) -> int:
        worker = self.bus.worker(
            {START_RUN: start_run_handler(self.store, self.executor, bus=self.bus, policy=self.policy)}
        )
        processed = 0
        while await worker.run_once() is not None:
            processed += 1
        return processed


async def _stack(scratch: Scratch, executor: StubExecutor, policy: RunPolicy | None = None) -> Stack:
    store = connect(scratch.uri, scratch.name)
    bus = run_bus(store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
    await bus.ensure_indexes()
    await RunStore(store).ensure_indexes()
    await TenantCheckpointSaver(store).ensure_indexes()
    return Stack(store, bus, executor, policy)


def _with_stack(
    scratch: Scratch,
    executor: StubExecutor,
    body: Callable[[Stack], Awaitable[Any]],
    policy: RunPolicy | None = None,
) -> Any:
    async def go() -> Any:
        stack = await _stack(scratch, executor, policy)
        try:
            return await body(stack)
        finally:
            await stack.store.close()

    return asyncio.run(go())


def _overlap(x: dict[str, Any], y: dict[str, Any]) -> bool:
    return bool(x["started_at"] < y["finished_at"] and y["started_at"] < x["finished_at"])


def test_a_diamond_started_through_the_outbox_runs_b_and_c_in_parallel_and_d_after_both(scratch: Scratch) -> None:
    async def body(stack: Stack) -> str:
        run = await stack.start()
        assert await stack.drain() == 1
        return str(run["_id"])

    run_id = _with_stack(scratch, StubExecutor(delay=0.3), body)
    tasks = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
    a, b, c, d = (tasks[t] for t in "abcd")
    assert {t["status"] for t in (a, b, c, d)} == {"done"}
    assert _overlap(b, c), (b["started_at"], b["finished_at"], c["started_at"], c["finished_at"])
    assert b["started_at"] >= a["finished_at"]
    assert c["started_at"] >= a["finished_at"]
    assert d["started_at"] >= max(b["finished_at"], c["finished_at"])
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["status"] == "completed"
    [job] = scratch.find("jobs", {"run_id": run_id})
    assert (job["status"], job["consumer"], job["org_id"]) == ("done", START_RUN, ORG_A)


def test_the_checkpoint_thread_id_is_org_id_colon_run_id_and_every_checkpoint_carries_the_org(
    scratch: Scratch,
) -> None:
    async def body(stack: Stack) -> str:
        run = await stack.start()
        await stack.drain()
        return str(run["_id"])

    # A completed run's checkpoints are deleted (RUN-2); a blocked run's stay, so block this one.
    blocking = RunPolicy(max_attempts=1, backoff_base=0.0, backoff_max=0.0)
    run_id = _with_stack(scratch, StubExecutor(fail=frozenset({"b"})), body, blocking)
    checkpoints = scratch.find("run_checkpoints")
    writes = scratch.find("run_checkpoint_writes")
    assert checkpoints
    assert writes
    assert {d["thread_id"] for d in checkpoints + writes} == {f"{ORG_A}:{run_id}"}
    assert {d["org_id"] for d in checkpoints + writes} == {ORG_A}
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["thread_id"] == f"{ORG_A}:{run_id}"


def test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published(scratch: Scratch) -> None:
    v1, v2 = diamond_template(version=1), diamond_template(version=2, extra_task=True)
    executor = StubExecutor()

    async def body(stack: Stack) -> tuple[str, str]:
        first = await stack.start(template=v1)
        newer = await stack.start(template=v2)  # v2 (with task `g`) exists before the first run executes
        await stack.drain()
        return str(first["_id"]), str(newer["_id"])

    first_id, newer_id = _with_stack(scratch, executor, body)
    first = scratch.find_one("onboarding_runs", {"_id": first_id})
    newer = scratch.find_one("onboarding_runs", {"_id": newer_id})
    assert first is not None
    assert newer is not None
    assert (first["template_version"], first["status"]) == (1, "completed")
    assert first["dag_hash"] == compile_dag(v1, hire_variables_for()).hash
    assert sorted(t["task_id"] for t in scratch.find("run_tasks", {"run_id": first_id})) == list("abcdef")
    assert sorted(t["task_id"] for t in scratch.find("run_tasks", {"run_id": newer_id})) == list("abcdefg")
    assert newer["template_version"] == 2
    assert executor.calls.count("g") == 1  # only the v2 run has it


def test_a_start_whose_audit_write_fails_leaves_no_run_task_event_or_job(scratch: Scratch) -> None:
    async def body(stack: Stack) -> None:
        with pytest.raises(RuntimeError, match="audit"):
            await stack.start(auditor=FailingAudit())

    _with_stack(scratch, StubExecutor(), body)
    for collection in ("onboarding_runs", "run_tasks", "events", "jobs", "audit_log"):
        assert scratch.count(collection) == 0, collection


def test_a_start_is_audited_in_the_same_transaction(scratch: Scratch) -> None:
    async def body(stack: Stack) -> str:
        return str((await stack.start())["_id"])

    run_id = _with_stack(scratch, StubExecutor(), body)
    [entry] = scratch.find("audit_log")
    assert (entry["org_id"], entry["action"], entry["target"], entry["outcome"]) == (
        ORG_A,
        "run.start",
        f"run:{run_id}",
        AuditOutcome.SUCCESS.value,
    )
    assert entry["actor"] == {"type": Actor.user("u_hr").type.value, "id": "u_hr"}


def test_a_redelivered_start_executes_no_task_twice(scratch: Scratch) -> None:
    executor = StubExecutor()

    async def body(stack: Stack) -> str:
        run = await stack.start()
        await stack.drain()
        # The same event delivered again (at least once): a fresh job for the same run.
        with tenant_scope(ORG_A):
            again = await stack.bus.publish(
                "run.started", {"run_id": run["_id"]}, idempotency_key=f"redeliver:{run['_id']}", run_id=run["_id"]
            )
        assert not again.duplicate
        assert await stack.drain() == 1
        # The fixed key itself is never written twice.
        with tenant_scope(ORG_A):
            duplicate = await stack.bus.publish(
                "run.started", {"run_id": run["_id"]}, idempotency_key=f"run:{run['_id']}:started", run_id=run["_id"]
            )
        assert duplicate.duplicate
        return str(run["_id"])

    run_id = _with_stack(scratch, executor, body)
    assert sorted(executor.calls) == list("abcdef")
    run = scratch.find_one("onboarding_runs", {"_id": run_id})
    assert run is not None
    assert run["status"] == "completed"


def test_two_orgs_runs_execute_each_in_its_own_org(scratch: Scratch) -> None:
    async def body(stack: Stack) -> tuple[str, str]:
        a = await stack.start(org=ORG_A)
        b = await stack.start(org=ORG_B)
        assert await stack.drain() == 2
        return str(a["_id"]), str(b["_id"])

    blocking = RunPolicy(max_attempts=1, backoff_base=0.0, backoff_max=0.0)  # blocked runs keep their checkpoints
    a_id, b_id = _with_stack(scratch, StubExecutor(fail=frozenset({"b"})), body, blocking)
    for org, run_id in ((ORG_A, a_id), (ORG_B, b_id)):
        assert {t["org_id"] for t in scratch.find("run_tasks", {"run_id": run_id})} == {org}
        assert {d["org_id"] for d in scratch.find("run_checkpoints", {"thread_id": f"{org}:{run_id}"})} == {org}
    assert scratch.count("run_checkpoints", {"thread_id": f"{ORG_B}:{a_id}"}) == 0
