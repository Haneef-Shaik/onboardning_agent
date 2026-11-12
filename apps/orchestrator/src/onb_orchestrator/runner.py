"""One task's step in a run (PRD FR-R2, FR-R3).

`RunExecutor.run_task` is one graph node's work. It moves the task along the state machine, never
around it: `pending → ready` only when every dependency is `done` or `skipped` (re-checked here, not
only by the graph's edges), then `running`, then `done` or `failed`. A skipped task never executes,
a task already `done` is not executed again (at-least-once delivery), and a task found `running` is
never executed by this delivery: only the delivery that moved it to `running` executes it, so a task's
work runs at most once per attempt. (A task left `running` by a crashed worker needs a per-task lease
to be taken over; that recovery belongs to RUN-2.) Each task runs in a span
continued from the traceparent stored on the run (F-4), so every task joins the trace of the request
that started the run.

Errors carry their type only, never a message (messages can hold personal data):
- `TaskFailedError`: the task's work failed and the task is `failed`.
- `TaskBlockedError`: the task cannot run now (it waits for an approval, or a dependency is not
  finished although the graph reached it). Deterministic: retrying does not help.
- `TaskInProgressError`: another delivery is running the task. Transient: a later retry finds it
  finished.
- `TaskStepError`: what a graph node raises for any other error, by type, marked `transient` for a
  database error, a lost compare-and-set race or a task in progress (the bus should retry the job),
  and not otherwise.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from onb_agents.runs import RunStore, TaskState, TransitionConflictError, dependencies_met
from onb_data import DataStore, current_tenant
from onb_telemetry import continue_from, span_attributes
from pymongo.errors import PyMongoError

from .executor import TaskExecutor, TaskWork

__all__ = [
    "RunExecutor",
    "TaskBlockedError",
    "TaskFailedError",
    "TaskInProgressError",
    "TaskStepError",
    "step_error",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TaskFailedError(Exception):
    """A task ended `failed`. Carries the error type only."""


class TaskBlockedError(RuntimeError):
    """The task cannot run now; retrying the job does not change that."""


class TaskInProgressError(Exception):
    """Another delivery moved the task to `running` and executes it; this one must not."""


class TaskStepError(Exception):
    """A graph node's error other than a task failure, by type only."""

    def __init__(self, type_name: str, *, transient: bool) -> None:
        super().__init__(type_name)
        self.type_name = type_name
        self.transient = transient


_TRANSIENT = (PyMongoError, TransitionConflictError, TaskInProgressError)


def step_error(error: Exception) -> TaskStepError:
    """The type-only stand-in for `error`; transient for database errors and compare-and-set races."""
    return TaskStepError(type(error).__name__, transient=isinstance(error, _TRANSIENT))


class RunExecutor:
    def __init__(self, store: DataStore, executor: TaskExecutor, clock: Callable[[], datetime] = _utc_now) -> None:
        self.store = store
        self.runs = RunStore(store)
        self._executor = executor
        self.clock = clock

    async def run_task(self, run_id: str, task_id: str, *, traceparent: str | None) -> None:
        task = await self.runs.task(run_id, task_id)
        if task is None:
            raise LookupError("no such task in this run")
        status = TaskState(task["status"])
        if status in (TaskState.DONE, TaskState.SKIPPED):
            return
        if status in (TaskState.FAILED, TaskState.CANCELLED):
            raise TaskFailedError(status.value)
        if status is TaskState.RUNNING:
            raise TaskInProgressError("another delivery is running this task")
        if status is TaskState.WAITING_APPROVAL:
            raise TaskBlockedError("the task waits for an approval; only the approval resumes it")
        if status is TaskState.PENDING:
            states = {t["task_id"]: t["status"] for t in await self.runs.tasks(run_id)}
            if not dependencies_met(task["depends_on"], states):
                raise TaskBlockedError("a task's dependencies are not all done or skipped")
            task = await self.runs.move_task(run_id, task_id, TaskState.READY, at=self.clock(), expected=task)
        # Only the delivery whose compare-and-set moves the task to `running` executes it.
        task = await self.runs.move_task(run_id, task_id, TaskState.RUNNING, at=self.clock(), expected=task)
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
            await self.runs.move_task(run_id, task["task_id"], TaskState.FAILED, at=self.clock(), expected=task)
            raise TaskFailedError(failure)
        await self.runs.move_task(run_id, task["task_id"], TaskState.DONE, at=self.clock(), expected=task)
