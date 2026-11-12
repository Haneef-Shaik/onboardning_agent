"""The orchestrator's consumers: execute a run, resume it, clean up after it (PRD FR-R1-R6, §7.2, §7.9).

`start_run_handler(store, executor)` is the event-bus handler for `run.started` and `run.resumed`
(both carry `{run_id}`). The worker has set the job's tenant context; the handler reads the run in that
org only (another org's run, or a missing one, is nothing to do), checks its stored DAG against its
hash, builds the LangGraph `StateGraph` and runs it on the checkpoint thread `org_id:run_id`.

Delivery is at least once, so the handler is idempotent: a run that is not `started` or `running`
(paused, blocked, cancelled or finished: HR or a finished run owns it) is left alone, a run with a
checkpoint resumes from it, and each task step skips work already done.

How a run ends or stops:
- every task done or skipped → `completed`, and its checkpoint is deleted;
- a task failed past its attempts → the run is `blocked` and `run.blocked` is published in the same
  transaction, so HR is told (the in-app consumer) exactly once. The checkpoint stays: HR retries or
  completes the task by hand and the run resumes where it stopped;
- the DAG does not match its hash, or any other deterministic error → `failed`, every unfinished task is
  `cancelled`, the checkpoint is deleted;
- HR paused or cancelled the run while this ran → the step in hand finishes, nothing new starts, and
  the handler returns: the control route owns the run's state. A cancelled run's checkpoint is deleted;
- this worker is draining (SIGTERM) → `ReleaseJob`: the task in hand finished, the job goes back to the
  queue at once with no attempt used, and the next worker drives the run on;
- a transient error (the database, a lost compare-and-set race, a task another delivery holds) is
  raised, so the bus retries the job (and dead-letters it after its last attempt).

A run already finished by an overlapping delivery is not an error. Logs carry ids and error types only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_agents.runs import (
    RUN_BLOCKED_KIND,
    IllegalTransitionError,
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    control_key,
    thread_id,
)
from onb_contracts.notify import RUN_BLOCKED
from onb_data import DataStore, current_tenant
from onb_eventbus import EventBus, Handler, JobContext, ReleaseJob
from onb_telemetry import continue_from, span_attributes
from pymongo.errors import PyMongoError

from .checkpointer import TenantCheckpointSaver
from .executor import TaskExecutor
from .graph import RunGraphState, build_run_graph
from .policy import RunPolicy
from .runner import (
    DrainRequestedError,
    RunExecutor,
    RunHaltedError,
    Sleep,
    TaskExhaustedError,
    TaskStepError,
)

__all__ = ["cleanup_run_handler", "fail_task_handler", "start_run_handler"]

_log = logging.getLogger("onb_orchestrator")
_MAX_RUN_ID: Final = 128
# The run states this consumer drives. Any other (paused, blocked, cancelled, finished) is left alone.
_DRIVEN: Final = frozenset({RunState.STARTED.value, RunState.RUNNING.value})
_FINISHED: Final = frozenset({RunState.COMPLETED.value, RunState.FAILED.value, RunState.CANCELLED.value})
_DONE_OR_SKIPPED: Final = frozenset({"done", "skipped"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id(payload: Mapping[str, Any]) -> str:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not 0 < len(run_id) <= _MAX_RUN_ID:
        raise ValueError(f"a run event payload needs a run_id of 1-{_MAX_RUN_ID} characters")
    return run_id


class _Driver:
    """One handler's collaborators: the runner (and through it the store), the bus and the saver."""

    def __init__(self, runner: RunExecutor, bus: EventBus) -> None:
        self.runner = runner
        self.bus = bus
        self.saver = TenantCheckpointSaver(runner.store)

    async def drive(self, run: Mapping[str, Any]) -> None:
        run_id = str(run["_id"])
        built = build_run_graph(run, self.runner, self.saver)
        if run["status"] == RunState.STARTED.value:
            await self.runner.runs.move_run(
                run_id, RunState.RUNNING, at=self.runner.clock(), expected_status=RunState.STARTED
            )  # not over a pause that arrived since the run was read
        state = await built.graph.aget_state(built.config)
        start: RunGraphState | None = None if state.next or state.values else {"finished": []}
        await built.graph.ainvoke(start, built.config)

    async def is_moot(self, run_id: str) -> bool:
        """True when the run is no longer ours to drive: HR paused, blocked or cancelled it, or it finished.
        A cancelled run's checkpoint goes with it."""
        current = await self.runner.runs.get(run_id)
        if current is None or current.get("status") in _DRIVEN:
            return False
        if current.get("status") == RunState.CANCELLED.value:
            await self.cleanup(run_id)
        return True

    async def cleanup(self, run_id: str) -> None:
        """Delete the run's checkpoints. A failure here never undoes the run's end: they are only a cache."""
        try:
            await self.saver.adelete_thread(thread_id(current_tenant().org_id, run_id))
        except PyMongoError as error:
            _log.warning("checkpoint cleanup failed", extra={"run_id": run_id, "error_type": type(error).__name__})

    async def complete(self, run_id: str) -> None:
        tasks = await self.runner.runs.tasks(run_id)
        if any(task["status"] not in _DONE_OR_SKIPPED for task in tasks):
            await self.fail(run_id, "IncompleteRun")
            return
        await self.finish(run_id, RunState.COMPLETED)
        await self.cleanup(run_id)

    async def fail(self, run_id: str, error_type: str) -> None:
        _log.warning("run %s failed: %s", run_id, error_type, extra={"run_id": run_id, "error_type": error_type})
        await self.finish(run_id, RunState.FAILED)
        await self.cleanup(run_id)

    async def finish(self, run_id: str, target: RunState) -> None:
        """Move the run to its end state; a run another delivery already finished is left as it is."""
        runner = self.runner
        if target is RunState.FAILED:
            await runner.runs.cancel_unfinished(run_id, at=runner.clock())
        try:
            await runner.runs.move_run(run_id, target, at=runner.clock())
        except (IllegalTransitionError, TransitionConflictError):
            current = await runner.runs.get(run_id)
            if current is None or current.get("status") not in _FINISHED:
                raise

    async def block(self, run_id: str, task_id: str) -> None:
        """A task failed past its attempts: `running → blocked` and `run.blocked`, in one transaction."""
        runner = self.runner
        run = await runner.runs.get(run_id)
        if run is None or run.get("status") != RunState.RUNNING.value:
            return
        key = control_key(run_id, RUN_BLOCKED_KIND, len(run.get("history", ())))

        async def change() -> None:
            await runner.runs.move_run(run_id, RunState.BLOCKED, at=runner.clock(), expected_status=RunState.RUNNING)

        _log.warning("run blocked", extra={"run_id": run_id, "task_id": task_id})
        try:
            await self.bus.publish(
                RUN_BLOCKED, {"run_id": run_id, "task_id": task_id}, idempotency_key=key, run_id=run_id, change=change
            )
        except (IllegalTransitionError, TransitionConflictError):
            if not await self.is_moot(run_id):
                raise


def start_run_handler(
    store: DataStore,
    executor: TaskExecutor,
    clock: Callable[[], datetime] = _utc_now,
    *,
    bus: EventBus | None = None,
    policy: RunPolicy | None = None,
    drain: asyncio.Event | None = None,
    sleep: Sleep | None = None,
) -> Handler:
    """The handler for `run.started` and `run.resumed`. `bus` is the platform's run bus (built here when
    not given); `drain` is set when the worker is shutting down."""
    from onb_agents.runs import run_bus

    runner = RunExecutor(store, executor, clock, policy=policy, drain=drain, sleep=sleep)
    driver = _Driver(runner, bus if bus is not None else run_bus(store))

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await runner.runs.get(run_id)
        if run is None:
            _log.info("run event for a run this org does not have; nothing to do", extra={"job_id": job.job_id})
            return
        if run.get("status") not in _DRIVEN:
            return
        attributes = span_attributes(org_id=current_tenant().org_id, run_id=run_id)
        try:
            # The run's span: every task span is its child (PRD §7.7), and it continues the request's trace.
            with continue_from(run.get("traceparent"), "orchestrator.run", attributes=attributes):
                await driver.drive(run)
        except DrainRequestedError:
            raise ReleaseJob from None
        except RunHaltedError:
            if not await driver.is_moot(run_id):
                # A task was cancelled under a run that is still running (a failure that cancelled the
                # tasks but not the run): nothing can drive it, so end it instead of leaving it running.
                await driver.fail(run_id, "TaskCancelled")
            return
        except TaskExhaustedError as error:
            await driver.block(run_id, error.task_id)
            return
        except PyMongoError:
            raise  # transient: the bus retries the job
        except (TaskStepError, TransitionConflictError) as error:
            if await driver.is_moot(run_id):
                return  # HR changed the run under it: the compare-and-set that lost is not an error
            if isinstance(error, TaskStepError) and not error.transient:
                await driver.fail(run_id, error.type_name)
                return
            raise  # transient: the bus retries the job
        except Exception as error:
            if await driver.is_moot(run_id):
                return
            await driver.fail(run_id, type(error).__name__)
            return
        try:
            await driver.complete(run_id)
        except (IllegalTransitionError, TransitionConflictError):
            if not await driver.is_moot(run_id):  # HR paused or blocked it while its last task finished
                raise

    return handle


# --- orchestrator.fail_task: a sink refused a task's grant (NOTIF-1, ADR 0013) -------------------------

SINK_REJECTED_KIND: Final = "sink_rejected"
_REASON: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PAYLOAD_ERROR: Final = "a task.sink_rejected payload needs a run_id, a reason code and an optional task_id/agent"


def _text(payload: Mapping[str, Any], name: str, *, required: bool) -> str | None:
    value = payload.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_RUN_ID:
        raise ValueError(_PAYLOAD_ERROR)
    return value


def _rejection(payload: Mapping[str, Any]) -> tuple[str, str | None, str | None, str]:
    """(run_id, task_id, agent, reason) of a `task.sink_rejected` payload; `ValueError` for any other shape."""
    reason = _text(payload, "reason", required=True)
    run_id = _text(payload, "run_id", required=True)
    if reason is None or run_id is None or not _REASON.fullmatch(reason):
        raise ValueError(_PAYLOAD_ERROR)
    return run_id, _text(payload, "task_id", required=False), _text(payload, "agent", required=False), reason


async def _fail_running(runs: RunStore, run_id: str, task_id: str, reason: str, at: datetime) -> bool:
    """Fail one task if it is `running`, with the sink's reason on it. A task that finished meanwhile
    (its own move won the race) stays as it is."""
    return await runs.fail_running(run_id, task_id, failure={"kind": SINK_REJECTED_KIND, "reason": reason}, at=at)


def fail_task_handler(store: DataStore, clock: Callable[[], datetime] = _utc_now) -> Handler:
    """The `orchestrator.fail_task` consumer: a sink refused a grant, so the task it was for fails.

    The task is the payload's `task_id` or, when the sink did not know it, every `running` task of the
    run for the payload's agent (every running task when no agent is named): fail closed. Only `running`
    tasks fail, since a grant exists only for a task that started; finished tasks and other orgs' runs
    are left alone (the read is tenant-scoped, so a foreign run id is a run that does not exist). The
    run itself fails when its driver sees the failed task (`orchestrator.start_run`)."""
    runs = RunStore(store)

    async def handle(job: JobContext) -> None:
        run_id, task_id, agent, reason = _rejection({**job.payload})
        if await runs.get(run_id) is None:
            _log.info("task.sink_rejected for a run this org does not have; nothing to do")
            return
        if task_id is not None:
            targets = [task_id]
        else:
            targets = [
                t["task_id"]
                for t in await runs.tasks(run_id)
                if t["status"] == TaskState.RUNNING.value and agent in (None, t["agent"])
            ]
        for target in targets:
            if await _fail_running(runs, run_id, target, reason, clock()):
                _log.warning("task failed by a sink rejection", extra={"run_id": run_id, "task_id": target})


def cleanup_run_handler(store: DataStore) -> Handler:
    """The handler for `run.cancelled`: delete the checkpoints of a run that is cancelled. A run that is
    anything else is left alone (the event names a run, it does not make it cancelled)."""
    runs = RunStore(store)
    saver = TenantCheckpointSaver(store)

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await runs.get(run_id)
        if run is None or run.get("status") != RunState.CANCELLED.value:
            return
        await saver.adelete_thread(thread_id(current_tenant().org_id, run_id))

    return handle
