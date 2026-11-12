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
- `TaskBlockedError`: the task cannot run now (a dependency is not finished although the graph
  reached it). Deterministic: retrying does not help.
- `TaskAwaitsApprovalError`: the task waits for an approval (HIL-1: an approval task, a tool call that
  needs one, an agent that needs a human). Not an error of the run: the graph node pauses with
  `interrupt()` and `settle` finishes the task once the approval is decided.
- `TaskInProgressError`: another delivery holds the task. Transient.
- `RunHaltedError` / `DrainRequestedError`: control flow, not failures. `TaskCancelledError` (a
  `RunHaltedError`) says the task itself is `cancelled`, which only a run's end does; a plain
  `RunHaltedError` says only that the run was not `running` when it was read (FIX-1).
- `TaskStepError`: what a graph node raises for any other error, by type, marked `transient` for a
  database error, a lost compare-and-set race, a task in progress or an approval not decided yet, and not
  otherwise.

**Approvals (HIL-1, ADR 0020).** An `approval` task, an agent that answers `needs_human` and a tool call the
hub refused under a `requires_approval` rule open an approval and park the task in `waiting_approval` (the
lease is released with the move). A rejection fails the task and the run (a person said no: nothing to retry).
An approved tool call runs the task again under a new lease, in the same attempt.
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

from onb_agents.approvals import ApprovalSpec, ApprovalStore, resolve_approver
from onb_agents.runs import (
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    dependencies_met,
    lease_expired,
    run_bus,
    stored_dag,
)
from onb_contracts.approvals import APPROVAL_REQUESTED, ApprovalKind
from onb_data import DataStore, current_tenant
from onb_eventbus import EventBus
from onb_telemetry import continue_from, span_attributes
from opentelemetry import trace
from pymongo.errors import PyMongoError

from .executor import ApprovalRequestedError, TaskExecutor, TaskWork
from .policy import RunPolicy

__all__ = [
    "MAX_APPROVALS_PER_TASK",
    "DrainRequestedError",
    "RunExecutor",
    "RunHaltedError",
    "Sleep",
    "TaskAwaitsApprovalError",
    "TaskBlockedError",
    "TaskCancelledError",
    "TaskExhaustedError",
    "TaskFailedError",
    "TaskInProgressError",
    "TaskNotSettledError",
    "TaskStepError",
    "step_error",
]

LEASE_EXPIRED = "LeaseExpired"
MAX_APPROVALS_PER_TASK = 2
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


class TaskAwaitsApprovalError(Exception):
    """The task is `waiting_approval` on `approval_id`: the graph node pauses with `interrupt()`."""

    def __init__(self, approval_id: str) -> None:
        super().__init__("the task waits for an approval")
        self.approval_id = approval_id


class TaskNotSettledError(Exception):
    """The graph resumed but the task's approval is not decided (yet). Transient: a later retry finds it."""


class TaskInProgressError(Exception):
    """Another delivery holds the task's lease and executes it; this one must not."""


class RunHaltedError(Exception):
    """The run is not `running` (paused, blocked, cancelled or finished): start nothing."""


class TaskCancelledError(RunHaltedError):
    """The task is `cancelled`: a run that failed or was cancelled cancelled it. Unlike a pause, a resume
    cannot bring it back."""


class DrainRequestedError(Exception):
    """This worker is shutting down: finish nothing new, hand the job back."""


class TaskStepError(Exception):
    """A graph node's error other than a task failure, by type only."""

    def __init__(self, type_name: str, *, transient: bool) -> None:
        super().__init__(type_name)
        self.type_name = type_name
        self.transient = transient


_TRANSIENT = (PyMongoError, TransitionConflictError, TaskInProgressError, TaskNotSettledError)


def step_error(error: Exception) -> TaskStepError:
    """The type-only stand-in for `error`; transient for database errors and compare-and-set races."""
    return TaskStepError(type(error).__name__, transient=isinstance(error, _TRANSIENT))


@dataclass(frozen=True, slots=True)
class _Failure:
    """Why an attempt failed: the error type (or the failure kind an executor reports) and whether another
    attempt can help. Only the type or kind, never a message."""

    error: str
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class _Asked:
    """The attempt did not fail: its work needs a human before the task can go on."""

    kind: ApprovalKind
    rule: str | None = None


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
        bus: EventBus | None = None,
    ) -> None:
        self.store = store
        self.runs = RunStore(store)
        self.approvals = ApprovalStore(store)
        self._bus: EventBus = bus if bus is not None else run_bus(store)
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
            raise TaskCancelledError("the task was cancelled")
        if status is TaskState.WAITING_APPROVAL:
            raise TaskAwaitsApprovalError(str(task["approval_id"]))
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
            if isinstance(failure, _Asked) or (failure is None and task["type"] == "approval"):
                kind: ApprovalKind = "task" if failure is None else failure.kind
                await self._wait_for_approval(run_id, task, kind, None if failure is None else failure.rule)
                return  # the approval was already decided: the task is settled
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

    async def _execute(self, run_id: str, task: Mapping[str, Any], traceparent: str | None) -> _Failure | _Asked | None:
        """Run the task's work under its lease. Returns the error type when it failed, what it asks of a human
        when it needs one (HIL-1), `None` on success."""
        task_id, token = task["task_id"], task["lease_token"]
        if task["type"] == "approval":  # open it first: the Approvals Agent reads the approval it summarises
            await self._open_approval(run_id, task, "task", None)
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
        failure: _Failure | _Asked | None = None
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
            except ApprovalRequestedError as asked:
                failure = _Asked(asked.kind, asked.rule)
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

    # --- approvals (HIL-1, ADR 0020) ---------------------------------------------------------------------

    async def _open_approval(
        self, run_id: str, task: Mapping[str, Any], kind: ApprovalKind, rule: str | None
    ) -> dict[str, Any]:
        """The approval this task waits on for this reason: opened once (a redelivery finds it)."""
        run = await self.runs.get(run_id)
        if run is None:
            raise LookupError("no such run")
        dag = stored_dag(run)
        template_task = next(t for t in dag["tasks"] if t["id"] == task["task_id"])
        hire = await self.store.tenant("new_hires").find_one({"_id": run["hire_id"]}) or {}
        approver_id, approver_role = resolve_approver(template_task.get("approver"), hire)
        items = [
            str(t["title"]) for t in dag["tasks"] if task["task_id"] in t["depends_on"] and t["status"] != "skipped"
        ]
        spec = ApprovalSpec(
            run_id=run_id,
            task_id=task["task_id"],
            hire_id=str(run["hire_id"]),
            kind=kind,
            rule=rule,
            approver_id=approver_id,
            approver_role=approver_role,
            title=str(task["title"]),
            items=items,
            sla_hours=task.get("sla_hours"),
        )
        return await self.approvals.create(spec, now=self.clock())

    async def _check_approval_limit(
        self, run_id: str, task: Mapping[str, Any], kind: ApprovalKind, rule: str | None
    ) -> None:
        """At most `MAX_APPROVALS_PER_TASK` approvals per task: LangGraph replays a node's earlier resume values,
        so a longer chain inside one node cannot be resumed reliably (ADR 0020). A task that needs more fails
        visibly instead of stalling the run."""
        existing = await self.approvals.for_task(run_id, task["task_id"])
        known = {a["kind"] + "\x1f" + str(a.get("rule") or "") for a in existing}
        if f"{kind}\x1f{rule or ''}" not in known and len(existing) >= MAX_APPROVALS_PER_TASK:
            await self._fail_for_approval(run_id, task, "approval_limit", str(len(existing)))

    async def _wait_for_approval(
        self, run_id: str, task: Mapping[str, Any], kind: ApprovalKind, rule: str | None
    ) -> None:
        """Park the running task in `waiting_approval` (leaving `running` releases its lease) and tell the
        approver, in one transaction; then raise `TaskAwaitsApprovalError` so the graph node pauses. An
        approval that is already decided (the task asked for the same thing again) settles the task instead."""
        await self._check_approval_limit(run_id, task, kind, rule)
        approval = await self._open_approval(run_id, task, kind, rule)
        if approval["status"] != "pending":
            await self._after_decision(run_id, task, approval, again=True, traceparent=None)
            return
        task_id = task["task_id"]

        async def park() -> None:
            await self.runs.move_task(
                run_id,
                task_id,
                TaskState.WAITING_APPROVAL,
                at=self.clock(),
                expected=task,
                fields={"approval_id": approval["_id"]},
            )

        payload = {"approval_id": approval["_id"], "run_id": run_id, "task_id": task_id}
        await self._bus.publish(
            APPROVAL_REQUESTED,
            payload,
            idempotency_key=f"approval:{approval['_id']}:requested",
            run_id=run_id,
            change=park,
        )
        raise TaskAwaitsApprovalError(str(approval["_id"]))

    async def is_decided(self, approval_id: str) -> bool:
        """True when the approval is no longer pending: its waiting task can be settled without a resume."""
        approval = await self.approvals.get(approval_id)
        return approval is not None and approval["status"] != "pending"

    async def settle(self, run_id: str, task_id: str, *, traceparent: str | None) -> None:
        """The graph resumed a task that waited: apply its approval's decision."""
        task = await self.runs.task(run_id, task_id)
        if task is None:
            raise LookupError("no such task in this run")
        if TaskState(task["status"]) in (TaskState.DONE, TaskState.SKIPPED):
            return
        if TaskState(task["status"]) is not TaskState.WAITING_APPROVAL:
            raise TaskBlockedError("the task does not wait for an approval")
        approval = await self.approvals.get(str(task["approval_id"]))
        if approval is None:
            raise LookupError("no such approval")
        if approval["status"] == "pending":
            raise TaskNotSettledError("the approval is not decided")
        await self._after_decision(run_id, task, approval, again=False, traceparent=traceparent)

    async def _after_decision(
        self,
        run_id: str,
        task: Mapping[str, Any],
        approval: Mapping[str, Any],
        *,
        again: bool,
        traceparent: str | None,
    ) -> None:
        """Finish the task from its decided approval. Approved lets it go on (a tool call runs the task
        again under a new lease, in the same attempt, now with its rule approved); anything else fails it.
        `again` is True when the task asked for an approval that was already decided: a refused call that was
        approved already is `approval_ineffective`, never another wait."""
        task_id = task["task_id"]
        if approval["status"] != "approved":
            decided = approval.get("decision") or {}
            await self._fail_for_approval(
                run_id, task, "approval_rejected", str(decided.get("decision", approval["status"]))
            )
        if approval["kind"] == "tool_call":
            if again:
                await self._fail_for_approval(run_id, task, "approval_ineffective", str(approval.get("rule")))
            running = await self.runs.move_task(
                run_id,
                task_id,
                TaskState.RUNNING,
                at=self.clock(),
                expected=task,
                fields={**self._lease(), "error": None, "retry_at": None, "failure": None},
            )
            await self._attempts(run_id, running, traceparent)
            return
        try:
            await self.runs.move_task(run_id, task_id, TaskState.DONE, at=self.clock(), expected=task)
        except TransitionConflictError:
            current = await self.runs.task(run_id, task_id)
            if current is None or current["status"] != TaskState.DONE.value:
                raise

    async def _fail_for_approval(self, run_id: str, task: Mapping[str, Any], kind: str, reason: str) -> None:
        """The task failed because a person said no (or the approval could not work): it is not retried, and
        the run fails (`TaskFailedError`, which the driver turns into a failed run)."""
        await self.runs.move_task(
            run_id,
            task["task_id"],
            TaskState.FAILED,
            at=self.clock(),
            expected=task,
            fields={"failure": {"kind": kind, "reason": reason}, "error": kind, "retry_at": None},
        )
        raise TaskFailedError(kind)
