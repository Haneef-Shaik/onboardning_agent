"""One task's step in a run (PRD FR-R2, FR-R3, FR-R4, FR-R6).

`RunExecutor.run_task` is one graph node's work. It moves the task along the state machine, never
around it: `pending → ready` only when every dependency is `done` or `skipped` (re-checked here, not
only by the graph's edges), then `running`, then `done` or `failed`. A skipped task never executes
and a task already `done` is not executed again (at-least-once delivery).

**Who runs a task.** Moving a task to `running` takes a lease (`lease_token`, `lease_until`), and the
holder renews it every third of `policy.lease_seconds` while the work runs. Only the delivery that
holds the lease executes the task and only it can finish it (the move is compare-and-set on the token).
A task found `running` is therefore one of two things:
- its lease is live: another delivery is running it, so this one raises `TaskInProgressError`
  (transient: a later retry finds it finished);
- its lease ran out, or was released: its worker is gone (killed, crashed, drained), so this delivery
  takes it over (`RunStore.take_over`, compare-and-set on the dead token, one more attempt) and runs it
  again with the same `idempotency_key`. A task whose effect already happened is not repeated, because
  every effect is idempotent on that key (`executor.TaskWork`).

**Retry and block (FR-R4).** An attempt that raises leaves the task `failed` with the error type and
the time of the next attempt (`retry_at`). While attempts remain the task waits out the backoff (a
drain or a pause interrupts the wait) and goes `failed → ready → running` again; at `max_attempts`
it stays `failed` and `TaskExhaustedError` tells the consumer to block the run. A takeover counts as
an attempt, so a task that keeps killing its workers blocks the run too (`LeaseExpired`).

**Control.** Before it does anything for a task, the step checks the run: a run that is not `running`
(paused, blocked, cancelled, finished) raises `RunHaltedError`, and a draining worker raises
`DrainRequestedError`. A task in hand is never preempted by a pause or a drain; it finishes.

Each task runs in a span continued from the traceparent stored on the run (F-4). Errors carry their type
only, never a message (messages can hold personal data):
- `TaskFailedError`: the task's work failed (a task found `failed`/`cancelled` by a delivery that may not retry).
- `TaskExhaustedError`: the task failed at its last attempt; `task_id` says which.
- `TaskBlockedError`: the task cannot run now (it waits for an approval, or a dependency is not
  finished although the graph reached it). Deterministic: retrying does not help.
- `TaskInProgressError`: another delivery holds the task. Transient.
- `RunHaltedError` / `DrainRequestedError`: control flow, not failures.
- `TaskStepError`: what a graph node raises for any other error, by type, marked `transient` for a
  database error, a lost compare-and-set race or a task in progress, and not otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from onb_agents.runs import (
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    dependencies_met,
    lease_expired,
)
from onb_data import DataStore, current_tenant
from onb_telemetry import continue_from, span_attributes
from opentelemetry import trace
from pymongo.errors import PyMongoError

from .executor import TaskExecutor, TaskWork
from .policy import RunPolicy

__all__ = [
    "DrainRequestedError",
    "RunExecutor",
    "RunHaltedError",
    "Sleep",
    "TaskBlockedError",
    "TaskExhaustedError",
    "TaskFailedError",
    "TaskInProgressError",
    "TaskStepError",
    "step_error",
]

LEASE_EXPIRED = "LeaseExpired"
Sleep = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def _task_span(traceparent: str | None, attributes: Mapping[str, str]) -> Iterator[trace.Span]:
    """The task's span: a child of the run's span when one is current (run → task), else a child of the
    run's stored traceparent (F-4), so a task always joins the trace of the request that started the run."""
    if trace.get_current_span().get_span_context().is_valid:
        with trace.get_tracer("onb_orchestrator").start_as_current_span(
            "orchestrator.task", attributes=attributes
        ) as span:
            yield span
    else:
        with continue_from(traceparent, "orchestrator.task", attributes=attributes) as span:
            yield span


class TaskFailedError(Exception):
    """A task ended `failed`. Carries the error type only."""


class TaskExhaustedError(Exception):
    """A task failed at its last attempt: the run must be blocked. `task_id` is an id, not data."""

    def __init__(self, task_id: str) -> None:
        super().__init__("a task failed at its last attempt")
        self.task_id = task_id


class TaskBlockedError(RuntimeError):
    """The task cannot run now; retrying the job does not change that."""


class TaskInProgressError(Exception):
    """Another delivery holds the task's lease and executes it; this one must not."""


class RunHaltedError(Exception):
    """The run is not `running` (paused, blocked, cancelled or finished): start nothing."""


class DrainRequestedError(Exception):
    """This worker is shutting down: finish nothing new, hand the job back."""


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


@dataclass(frozen=True, slots=True)
class _Failure:
    """Why an attempt failed: the error type (or the failure kind an executor reports) and whether another
    attempt can help. Only the type or kind, never a message."""

    error: str
    retryable: bool = True


def _failure_of(error: Exception) -> _Failure:
    """An executor error as a failed attempt. An executor that knows retrying cannot help (RUN-3's agent
    failures with a deterministic `kind`: no agent, a refused grant, an exhausted budget) says so with
    `retryable = False`, and the run is blocked at once instead of after `max_attempts` identical failures.
    The `kind` an executor reports is a code, so it is the better `error` for HR than the exception type."""
    kind = getattr(error, "kind", None)
    return _Failure(
        error=kind if isinstance(kind, str) and kind else type(error).__name__,
        retryable=getattr(error, "retryable", True) is not False,
    )


def _outside_failure(task: Mapping[str, Any]) -> str:
    """The error type of a task failed from outside: the kind of the failure its writer recorded."""
    recorded = task.get("failure") or {}
    kind = recorded.get("kind") if isinstance(recorded, Mapping) else None
    return str(kind) if kind else "FailedFromOutside"


def _new_token() -> str:
    return secrets.token_hex(16)


class RunExecutor:
    def __init__(
        self,
        store: DataStore,
        executor: TaskExecutor,
        clock: Callable[[], datetime] = _utc_now,
        *,
        policy: RunPolicy | None = None,
        drain: asyncio.Event | None = None,
        sleep: Sleep | None = None,
        new_token: Callable[[], str] = _new_token,
    ) -> None:
        self.store = store
        self.runs = RunStore(store)
        self._executor = executor
        self.clock = clock
        self.policy = policy or RunPolicy()
        self._drain = drain
        self._sleep = sleep
        self._new_token = new_token

    # --- one task ---------------------------------------------------------------------------------

    async def run_task(self, run_id: str, task_id: str, *, traceparent: str | None) -> None:
        task = await self.runs.task(run_id, task_id)
        if task is None:
            raise LookupError("no such task in this run")
        status = TaskState(task["status"])
        if status in (TaskState.DONE, TaskState.SKIPPED):
            return
        await self._ensure_runnable(run_id)
        if status is TaskState.CANCELLED:
            raise RunHaltedError("the task was cancelled")
        if status is TaskState.WAITING_APPROVAL:
            raise TaskBlockedError("the task waits for an approval; only the approval resumes it")
        if status is TaskState.RUNNING:
            running = await self._take_over(run_id, task)
        else:
            if status is TaskState.FAILED:
                task = await self._retry_failed(run_id, task)
            elif status is TaskState.PENDING:
                states = {t["task_id"]: t["status"] for t in await self.runs.tasks(run_id)}
                if not dependencies_met(task["depends_on"], states):
                    raise TaskBlockedError("a task's dependencies are not all done or skipped")
                task = await self.runs.move_task(run_id, task_id, TaskState.READY, at=self.clock(), expected=task)
            running = await self._claim(run_id, task)
        await self._attempts(run_id, running, traceparent)

    async def _ensure_runnable(self, run_id: str) -> None:
        if self._drain is not None and self._drain.is_set():
            raise DrainRequestedError("this worker is draining")
        run = await self.runs.get(run_id)
        if run is None or run.get("status") != RunState.RUNNING.value:
            raise RunHaltedError("the run is not running")

    def _lease(self) -> dict[str, Any]:
        return {
            "lease_token": self._new_token(),
            "lease_until": self.clock() + timedelta(seconds=self.policy.lease_seconds),
        }

    async def _claim(self, run_id: str, ready: Mapping[str, Any]) -> dict[str, Any]:
        """`ready → running`, taking the lease; only the delivery whose compare-and-set wins executes."""
        return await self.runs.move_task(
            run_id,
            ready["task_id"],
            TaskState.RUNNING,
            at=self.clock(),
            expected=ready,
            # A new attempt starts clean: the last attempt's error, backoff and recorded failure (RUN-3's
            # `failure`, a sink's cause) are cleared when the task re-enters `running`.
            fields={**self._lease(), "error": None, "retry_at": None, "failure": None},
        )

    async def _take_over(self, run_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
        """A `running` task: leave it to its live holder, or take it over from a holder that is gone."""
        now = self.clock()
        if not lease_expired(task, now):
            raise TaskInProgressError("another delivery is running this task")
        if int(task["attempts"]) >= self.policy.max_attempts:
            await self.runs.move_task(
                run_id,
                task["task_id"],
                TaskState.FAILED,
                at=now,
                expected=task,
                fields={"error": LEASE_EXPIRED, "retry_at": None},
            )
            raise TaskExhaustedError(task["task_id"])
        return await self.runs.take_over(run_id, task["task_id"], expected=task, at=now, **self._lease_args())

    def _lease_args(self) -> dict[str, Any]:
        lease = self._lease()
        return {"token": lease["lease_token"], "lease_until": lease["lease_until"]}

    async def _retry_failed(self, run_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
        """A `failed` task found by a delivery: with attempts left it waits out its backoff and goes
        `ready`; at its last attempt the run is blocked."""
        if int(task["attempts"]) >= self.policy.max_attempts:
            raise TaskExhaustedError(task["task_id"])
        await self._wait_until(task.get("retry_at"))
        await self._ensure_runnable(run_id)
        return await self.runs.move_task(run_id, task["task_id"], TaskState.READY, at=self.clock(), expected=task)

    # --- attempts ---------------------------------------------------------------------------------

    async def _attempts(self, run_id: str, running: dict[str, Any], traceparent: str | None) -> None:
        task = running
        while True:
            failure = await self._execute(run_id, task, traceparent)
            if failure is None:
                try:
                    await self.runs.move_task(run_id, task["task_id"], TaskState.DONE, at=self.clock(), expected=task)
                    return
                except TransitionConflictError:
                    # The task was moved while it ran. A sink's rejection fails it (`orchestrator.fail_task`)
                    # and a task failed from outside never turns `done`, whatever its agent reports.
                    outside = await self.runs.task(run_id, task["task_id"])
                    if outside is None or outside["status"] != TaskState.FAILED.value:
                        raise
                    failure = _Failure(_outside_failure(outside))
            attempts = int(task["attempts"])
            last = not failure.retryable or attempts >= self.policy.max_attempts
            retry_at = None if last else self.clock() + timedelta(seconds=self.policy.backoff(attempts))
            failed = await self._fail(run_id, task, failure.error, retry_at)
            if last:
                raise TaskExhaustedError(task["task_id"])
            await self._wait_until(retry_at)
            await self._ensure_runnable(run_id)
            ready = await self.runs.move_task(
                run_id, task["task_id"], TaskState.READY, at=self.clock(), expected=failed
            )
            task = await self._claim(run_id, ready)

    async def _fail(
        self, run_id: str, task: Mapping[str, Any], failure: str, retry_at: datetime | None
    ) -> dict[str, Any]:
        """The attempt failed: the task is `failed` with the error type. A task a sink already failed from
        outside (`orchestrator.fail_task`, RUN-3) is `failed` already and counts as this attempt's failure,
        exactly like an executor error: it is retried while attempts remain and blocks the run at the last."""
        try:
            return await self.runs.move_task(
                run_id,
                task["task_id"],
                TaskState.FAILED,
                at=self.clock(),
                expected=task,
                fields={"error": failure, "retry_at": retry_at},
            )
        except TransitionConflictError:
            outside = await self.runs.task(run_id, task["task_id"])
            if outside is None or outside["status"] != TaskState.FAILED.value:
                raise
            return outside

    async def _wait_until(self, moment: datetime | None) -> None:
        """Wait out a backoff. A drain ends the wait early (the next worker waits the rest)."""
        if moment is None:
            return
        seconds = (moment - self.clock()).total_seconds()
        if seconds <= 0:
            return
        if self._sleep is not None:
            await self._sleep(seconds)
        elif self._drain is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._drain.wait(), timeout=seconds)
        else:
            await asyncio.sleep(seconds)

    async def _execute(self, run_id: str, task: Mapping[str, Any], traceparent: str | None) -> _Failure | None:
        """Run the task's work under its lease. Returns the error type when it failed, `None` on success."""
        task_id, token = task["task_id"], task["lease_token"]
        attributes = span_attributes(
            org_id=current_tenant().org_id, run_id=run_id, task_id=task_id, agent_name=task["agent"]
        )
        work = TaskWork(
            run_id=run_id,
            task_id=task_id,
            agent=task["agent"],
            action=task["action"],
            type=task["type"],
            attempt=int(task["attempts"]),
            idempotency_key=f"{run_id}:{task_id}",
        )
        lost = asyncio.Event()
        failure: _Failure | None = None
        with _task_span(traceparent, attributes):
            # Started inside the span: the executor runs in a copy of this context, so its own spans (the
            # agent, its LLM and tool calls) are children of the task's span (run → task → agent → LLM/tool).
            working = asyncio.ensure_future(self._executor.execute(work))
            beating = asyncio.ensure_future(self._heartbeat(run_id, task_id, token, working, lost))
            try:
                await working
            except asyncio.CancelledError:
                if lost.is_set():
                    raise TaskInProgressError("the task's lease passed to another worker") from None
                # Cut off (a sibling failed, the worker is stopping): give the lease back so the next
                # worker takes the task at once instead of waiting for the lease to run out.
                await asyncio.shield(self._release(run_id, task_id, token, int(task["attempts"]) - 1))
                raise
            except PyMongoError:
                # A database error is not the task's failure: give the lease and the attempt back and let
                # the bus retry the job (the agent executor re-raises these on purpose, RUN-3).
                await asyncio.shield(self._release(run_id, task_id, token, int(task["attempts"]) - 1))
                raise
            except Exception as error:  # any other executor error is this attempt's failure: retry, then block
                failure = _failure_of(error)
            finally:
                beating.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beating
        if lost.is_set():
            raise TaskInProgressError("the task's lease passed to another worker")
        return failure

    async def _heartbeat(
        self, run_id: str, task_id: str, token: str, working: asyncio.Future[None], lost: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self.policy.heartbeat_seconds)
            until = self.clock() + timedelta(seconds=self.policy.lease_seconds)
            try:
                held = await self.runs.renew_lease(run_id, task_id, token, until)
            except Exception:  # noqa: S112 - any error: the lease still holds until it runs out; beat again
                continue
            if not held:
                lost.set()
                working.cancel()
                return

    async def _release(self, run_id: str, task_id: str, token: str, attempts: int) -> None:
        with contextlib.suppress(PyMongoError):
            await self.runs.release_lease(run_id, task_id, token, attempts=attempts)
