"""The `orchestrator.start_run` consumer: execute one run (PRD FR-R1-R3, §7.2, §7.9 Event bus).

`start_run_handler(store, executor)` is the event-bus handler for `run.started`. The worker has set
the job's tenant context; the handler reads the run in that org only (another org's run, or a missing
one, is nothing to do), checks its stored DAG against its hash, builds the LangGraph `StateGraph` and
runs it on the checkpoint thread `org_id:run_id`.

Delivery is at least once, so the handler is idempotent: a run that is not `started` or `running`
(finished, or in a state a later goal owns) is left alone, a run with a checkpoint resumes from it,
and each task step skips work already done.

How a run ends:
- every task done or skipped → `completed`;
- a task failed, the DAG does not match its hash, or any deterministic error (a blocked task, a
  broken graph) → `failed`, and every unfinished task is `cancelled` (including a sibling LangGraph
  cut off mid-step), so a failed run never shows a task still `running`;
- a transient error (the database, a lost compare-and-set race, a task another delivery is
  running) is raised, so the bus retries the job (and dead-letters it after its last attempt); the
  run is left for the retry.

A run already finished by an overlapping delivery is not an error. Retries and `blocked` (FR-R4)
belong to RUN-2. Logs carry ids and error types only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_agents.runs import IllegalTransitionError, RunState, RunStore, TaskState, TransitionConflictError
from onb_data import DataStore, current_tenant
from onb_eventbus import Handler, JobContext
from onb_telemetry import continue_from, span_attributes
from pymongo.errors import PyMongoError

from .executor import TaskExecutor
from .graph import RunGraphState, build_run_graph
from .runner import RunExecutor, TaskStepError

__all__ = ["fail_task_handler", "start_run_handler"]

_log = logging.getLogger("onb_orchestrator")
_MAX_RUN_ID: Final = 128
# The run states this consumer drives. Any other (finished, or a state a later goal owns) is left alone.
_DRIVEN: Final = frozenset({RunState.STARTED.value, RunState.RUNNING.value})
_FINISHED: Final = frozenset({RunState.COMPLETED.value, RunState.FAILED.value})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id(payload: Mapping[str, Any]) -> str:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not 0 < len(run_id) <= _MAX_RUN_ID:
        raise ValueError(f"a run.started payload needs a run_id of 1-{_MAX_RUN_ID} characters")
    return run_id


async def _finish(runner: RunExecutor, run_id: str, target: RunState) -> None:
    """Move the run to its end state; a run another delivery already finished is left as it is."""
    if target is RunState.FAILED:
        await runner.runs.cancel_unfinished(run_id, at=runner.clock())
    try:
        await runner.runs.move_run(run_id, target, at=runner.clock())
    except (IllegalTransitionError, TransitionConflictError):
        current = await runner.runs.get(run_id)
        if current is None or current.get("status") not in _FINISHED:
            raise


async def _drive(runner: RunExecutor, run: Mapping[str, Any]) -> None:
    run_id = str(run["_id"])
    built = build_run_graph(run, runner)
    if run["status"] == RunState.STARTED.value:
        await runner.runs.move_run(run_id, RunState.RUNNING, at=runner.clock())
    state = await built.graph.aget_state(built.config)
    start: RunGraphState | None = None if state.next or state.values else {"finished": []}
    await built.graph.ainvoke(start, built.config)


def start_run_handler(store: DataStore, executor: TaskExecutor, clock: Callable[[], datetime] = _utc_now) -> Handler:
    runner = RunExecutor(store, executor, clock)

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await runner.runs.get(run_id)
        if run is None:
            _log.info("run.started for a run this org does not have; nothing to do", extra={"job_id": job.job_id})
            return
        if run.get("status") not in _DRIVEN:
            return
        attributes = span_attributes(org_id=current_tenant().org_id, run_id=run_id)
        try:
            # The run's span: every task span is its child (PRD §7.7), and it continues the request's trace.
            with continue_from(run.get("traceparent"), "orchestrator.run", attributes=attributes):
                await _drive(runner, run)
        except (PyMongoError, TransitionConflictError):
            raise  # transient: the bus retries the job
        except TaskStepError as error:
            if error.transient:
                raise
            await _fail(runner, run_id, error.type_name)
            return
        except Exception as error:
            await _fail(runner, run_id, type(error).__name__)
            return
        await _finish(runner, run_id, RunState.COMPLETED)

    return handle


async def _fail(runner: RunExecutor, run_id: str, error_type: str) -> None:
    _log.warning("run failed", extra={"run_id": run_id, "error_type": error_type})
    await _finish(runner, run_id, RunState.FAILED)


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
    for _ in range(2):
        task = await runs.task(run_id, task_id)
        if task is None or task["status"] != TaskState.RUNNING.value:
            return False
        await runs.record_outcome(run_id, task_id, result=None, failure={"kind": SINK_REJECTED_KIND, "reason": reason})
        try:
            await runs.move_task(run_id, task_id, TaskState.FAILED, at=at, expected=task)
        except TransitionConflictError:
            continue
        return True
    return False


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

    return handle
