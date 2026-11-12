"""RUN-2 boxes 2 and 3 on the compose replica set: a task failing past its attempts blocks the run and the
real outbox queues the HR notification in the same transaction; resuming the run through the real bus
finishes it and deletes its checkpoint (PRD FR-R4, FR-R5, §7.2).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from onb_agents.runs import (
    RESUME_RUN,
    RUN_RESUMED,
    START_RUN,
    RunStarter,
    RunState,
    RunStore,
    TaskState,
    control_key,
    run_bus,
)
from onb_agents.runs.testing import diamond_template, hire_variables_for
from onb_contracts.notify import IN_APP_CONSUMER, RUN_BLOCKED
from onb_data import AuditLog, connect, tenant_scope
from onb_eventbus import QueueSettings
from onb_orchestrator.checkpointer import TenantCheckpointSaver
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.policy import RunPolicy

from .conftest import Scratch

ORG = "org_acme"
POLICY = RunPolicy(max_attempts=2, backoff_base=0.0, backoff_max=0.0)


def test_a_blocked_run_queues_the_hr_notification_with_the_block_and_resumes_through_the_bus(scratch: Scratch) -> None:
    async def go() -> str:
        store = connect(scratch.uri, scratch.name)
        try:
            bus = run_bus(store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
            await bus.ensure_indexes()
            runs = RunStore(store)
            await runs.ensure_indexes()
            await TenantCheckpointSaver(store).ensure_indexes()
            with tenant_scope(ORG):
                started = await RunStarter(runs, bus, AuditLog(store)).start(
                    template=diamond_template(), hire_id="h_1", variables=hire_variables_for(), actor_id="u_hr"
                )
            run_id = str(started.run["_id"])

            failing = StubExecutor(fail=frozenset({"b"}))
            worker = bus.worker({START_RUN: start_run_handler(store, failing, bus=bus, policy=POLICY)})
            while await worker.run_once() is not None:
                pass

            # What POST /runs/{id}/tasks/b/retry does: task ready, run running, `run.resumed` published.
            with tenant_scope(ORG):
                run = await runs.get(run_id) or {}
                assert run["status"] == "blocked"

                async def change() -> None:
                    now = datetime.now(UTC)
                    await runs.move_task(run_id, "b", TaskState.READY, at=now, fields={"attempts": 0})
                    await runs.move_run(run_id, RunState.RUNNING, at=now, expected_status=RunState.BLOCKED)

                await bus.publish(
                    RUN_RESUMED,
                    {"run_id": run_id},
                    idempotency_key=control_key(run_id, "resume", len(run["history"])),
                    run_id=run_id,
                    change=change,
                )
            healthy = StubExecutor()
            handler = start_run_handler(store, healthy, bus=bus, policy=POLICY)
            resumer = bus.worker({RESUME_RUN: handler})
            while await resumer.run_once() is not None:
                pass
            assert "a" not in healthy.calls
            return run_id
        finally:
            await store.close()

    run_id = asyncio.run(go())
    run = scratch.find_one("onboarding_runs", {"_id": run_id}) or {}
    assert run["status"] == "completed"
    [event] = scratch.find("events", {"run_id": run_id, "type": RUN_BLOCKED})
    assert (event["org_id"], event["payload"]) == (ORG, {"run_id": run_id, "task_id": "b"})
    [notification] = scratch.find("jobs", {"event_id": event["_id"]})
    assert (notification["consumer"], notification["org_id"]) == (IN_APP_CONSUMER, ORG)
    tasks = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
    assert tasks["b"]["status"] == "done"
    assert scratch.count("run_checkpoints", {"thread_id": f"{ORG}:{run_id}"}) == 0
    assert scratch.count("dead_letters") == 0
    transitions: list[Any] = [(h["from"], h["to"]) for h in tasks["b"]["history"]]
    assert transitions.count(("running", "failed")) == 2, "two attempts failed before the run blocked"
