"""Run one run: the `orchestrator.start_run` consumer and the per-task step (PRD FR-R2, FR-R3, §7.2).

`start_run_handler(store, executor)` is the event-bus handler for `run.started`. The worker has set
the job's tenant context; the handler reads the run in that org only (another org's run, or a missing
one, is nothing to do), checks its stored DAG against its hash, builds the LangGraph `StateGraph` and
runs it on the checkpoint thread `org_id:run_id`. Delivery is at least once, so it is idempotent: a
run that is not `started` or `running` (finished, or in a state a later goal owns) is left alone, a
run with a checkpoint resumes from it, and each task step skips work
already done. A task that fails marks the run `failed` (retries and blocking are RUN-2's).

`RunExecutor.run_task` is one graph node's work. It moves the task along the state machine, never
around it: `pending → ready` only when every dependency is `done` or `skipped` (re-checked here, not
only by the graph's edges), then `running`, then `done` or `failed`. A skipped task never executes.
Each task runs in a span continued from the traceparent stored on the run (F-4), so every task joins
the trace of the request that started the run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_agents.runs import (
    DagIntegrityError,
    RunState,
    RunStore,
    TaskState,
    dependencies_met,
)
from onb_data import DataStore, current_tenant
from onb_eventbus import Handler, JobContext
from onb_telemetry import continue_from, span_attributes

from .executor import TaskExecutor, TaskWork

__all__ = ["RunExecutor", "TaskFailedError", "start_run_handler"]

_log = logging.getLogger("onb_orchestrator")
_MAX_RUN_ID: Final = 128
# The run states this consumer drives. Any other (finished, or a state a later goal owns) is left alone.
_DRIVEN: Final = frozenset({RunState.STARTED.value, RunState.RUNNING.value})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TaskFailedError(Exception):
    """A task ended `failed`. Carries the error type only: messages can hold personal data."""


class RunExecutor:
    def __init__(self, store: DataStore, executor: TaskExecutor, clock: Callable[[], datetime] = _utc_now) -> None:
        self.store = store
        self.runs = RunStore(store)
        self._executor = executor
        self._clock = clock

    async def run_task(self, run_id: str, task_id: str, *, traceparent: str | None) -> None:
        task = await self.runs.task(run_id, task_id)
        if task is None:
            raise LookupError("no such task in this run")
        status = TaskState(task["status"])
        if status in (TaskState.DONE, TaskState.SKIPPED):
            return
        if status in (TaskState.FAILED, TaskState.CANCELLED):
            raise TaskFailedError(status.value)
        if status is TaskState.WAITING_APPROVAL:
            raise RuntimeError("the task waits for an approval; only the approval resumes it")
        if status is TaskState.PENDING:
            states = {t["task_id"]: t["status"] for t in await self.runs.tasks(run_id)}
            if not dependencies_met(task["depends_on"], states):
                raise RuntimeError("a task's dependencies are not all done or skipped")
            task = await self.runs.move_task(run_id, task_id, TaskState.READY, at=self._clock(), expected=task)
        if task["status"] == TaskState.READY.value:
            task = await self.runs.move_task(run_id, task_id, TaskState.RUNNING, at=self._clock(), expected=task)
        await self._execute(run_id, task, traceparent)

    async def _execute(self, run_id: str, task: Mapping[str, Any], traceparent: str | None) -> None:
        attributes = span_attributes(
            org_id=current_tenant().org_id, run_id=run_id, task_id=task["task_id"], agent_name=task["agent"]
        )
        work = TaskWork(
            run_id=run_id,
            task_id=task["task_id"],
            agent=task["agent"],
            action=task["action"],
            type=task["type"],
            attempt=int(task["attempts"]),
        )
        failure: str | None = None
        with continue_from(traceparent, "orchestrator.task", attributes=attributes):
            try:
                await self._executor.execute(work)
            except Exception as error:
                failure = type(error).__name__
        if failure is not None:
            await self.runs.move_task(run_id, task["task_id"], TaskState.FAILED, at=self._clock(), expected=task)
            raise TaskFailedError(failure)
        await self.runs.move_task(run_id, task["task_id"], TaskState.DONE, at=self._clock(), expected=task)


def _run_id(payload: Mapping[str, Any]) -> str:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not 0 < len(run_id) <= _MAX_RUN_ID:
        raise ValueError(f"a run.started payload needs a run_id of 1-{_MAX_RUN_ID} characters")
    return run_id


def start_run_handler(store: DataStore, executor: TaskExecutor, clock: Callable[[], datetime] = _utc_now) -> Handler:
    from .graph import RunGraphState, build_run_graph  # the graph module imports this one

    runner = RunExecutor(store, executor, clock)

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await runner.runs.get(run_id)
        if run is None:
            _log.info("run.started for a run this org does not have; nothing to do", extra={"job_id": job.job_id})
            return
        if run.get("status") not in _DRIVEN:
            return
        try:
            built = build_run_graph(run, runner)
        except DagIntegrityError:
            _log.error("a run's stored DAG does not match its hash; the run is failed", extra={"run_id": run_id})
            await runner.runs.move_run(run_id, RunState.FAILED, at=clock())
            return
        if run["status"] == RunState.STARTED.value:
            await runner.runs.move_run(run_id, RunState.RUNNING, at=clock())
        state = await built.graph.aget_state(built.config)
        start: RunGraphState | None = None if state.next or state.values else {"finished": []}
        try:
            await built.graph.ainvoke(start, built.config)
        except TaskFailedError:
            await runner.runs.move_run(run_id, RunState.FAILED, at=clock())
            return
        await runner.runs.move_run(run_id, RunState.COMPLETED, at=clock())

    return handle
