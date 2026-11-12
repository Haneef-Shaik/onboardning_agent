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

A run with a task waiting for an approval (HIL-1) is neither finished nor failed: its graph is paused
at `interrupt()` and the run stays `running` (nothing completes it). `orchestrator.resume_approval`
(`approval.decided`) resumes it from the stored decision, and `orchestrator.record_decision`
(`decision.received`, a Telegram press) records a decision made there exactly as the web route does. A
run that fails or is cancelled cancels the approvals still pending.

A run already finished by an overlapping delivery is not an error. Logs carry ids and error types only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from langgraph.types import Command
from onb_agents.approvals import (
    AlreadyDecidedError,
    ApprovalDecider,
    ApprovalNotFoundError,
    ApprovalStore,
    NotApproverError,
)
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
from onb_contracts.approvals import APPROVAL_REF_TYPE
from onb_contracts.notify import OUTBOX_COLLECTION, RUN_BLOCKED, Decision
from onb_data import AuditLog, Auditor, DataStore, current_tenant
from onb_eventbus import EventBus, Handler, JobContext, ReleaseJob
from onb_telemetry import continue_from, span_attributes
from pymongo.errors import PyMongoError

from .checkpointer import TenantCheckpointSaver
from .executor import TaskExecutor
from .graph import RunGraph, RunGraphState, build_run_graph
from .policy import RunPolicy
from .runner import (
    DrainRequestedError,
    RunExecutor,
    RunHaltedError,
    Sleep,
    TaskExhaustedError,
    TaskStepError,
)

__all__ = [
    "cleanup_run_handler",
    "fail_task_handler",
    "record_decision_handler",
    "resume_approval_handler",
    "start_run_handler",
]

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


async def _paused(built: RunGraph) -> bool:
    """A graph that stopped with nodes still to run is waiting at an `interrupt()` (an approval)."""
    return bool((await built.graph.aget_state(built.config)).next)


class _Driver:
    """One handler's collaborators: the runner (and through it the store), the bus and the saver."""

    def __init__(self, runner: RunExecutor, bus: EventBus) -> None:
        self.runner = runner
        self.bus = bus
        self.saver = TenantCheckpointSaver(runner.store)

    async def drive(self, run: Mapping[str, Any]) -> bool:
        """Run the graph from its start or its checkpoint. True when it paused for an approval."""
        run_id = str(run["_id"])
        built = build_run_graph(run, self.runner, self.saver)
        if run["status"] == RunState.STARTED.value:
            await self.runner.runs.move_run(
                run_id, RunState.RUNNING, at=self.runner.clock(), expected_status=RunState.STARTED
            )  # not over a pause that arrived since the run was read
        await self._settle_decided(built)
        state = await built.graph.aget_state(built.config)
        start: RunGraphState | None = None if state.next or state.values else {"finished": []}
        await built.graph.ainvoke(start, built.config)
        return await _paused(built)

    async def _settle_decided(self, built: RunGraph) -> None:
        """A task waiting at an interrupt whose approval was decided while nobody was driving the run (it was
        paused or blocked, or the decision beat the pause's checkpoint) is resumed first, so driving the graph
        on never pauses it again for a decision that was already made."""
        while True:
            state = await built.graph.aget_state(built.config)
            decided = [
                i
                for t in state.tasks
                for i in t.interrupts
                if isinstance(i.value, dict) and await self.runner.is_decided(str(i.value.get("approval_id")))
            ]
            if not decided:
                return
            approval_id = str(decided[0].value["approval_id"])
            await built.graph.ainvoke(Command(resume={decided[0].id: {"approval_id": approval_id}}), built.config)

    async def resume(self, run: Mapping[str, Any], approval_id: str) -> bool:
        """Resume the paused node of this approval from the checkpoint. True when the graph paused again."""
        built = build_run_graph(run, self.runner, self.saver)
        state = await built.graph.aget_state(built.config)
        waiting = [
            i
            for t in state.tasks
            for i in t.interrupts
            if isinstance(i.value, dict) and i.value.get("approval_id") == approval_id
        ]
        if not waiting:
            raise TaskStepError("InterruptNotCheckpointed", transient=True)  # the start job is still pausing
        # The resume value names the approval only: the node settles from the stored decision.
        await built.graph.ainvoke(Command(resume={waiting[0].id: {"approval_id": approval_id}}), built.config)
        return await _paused(built)

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
            await runner.approvals.cancel_pending(run_id, at=runner.clock())  # nothing left to decide (HIL-1)
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
    driver = _driver(store, executor, clock, bus, policy, drain, sleep)

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await driver.runner.runs.get(run_id)
        if run is None:
            _log.info("run event for a run this org does not have; nothing to do", extra={"job_id": job.job_id})
            return
        if run.get("status") not in _DRIVEN:
            return
        await _drive_guarded(driver, run, lambda: driver.drive(run))

    return handle


def _driver(
    store: DataStore,
    executor: TaskExecutor,
    clock: Callable[[], datetime],
    bus: EventBus | None,
    policy: RunPolicy | None,
    drain: asyncio.Event | None,
    sleep: Sleep | None,
) -> _Driver:
    from onb_agents.runs import run_bus

    the_bus = bus if bus is not None else run_bus(store)
    runner = RunExecutor(store, executor, clock, policy=policy, drain=drain, sleep=sleep, bus=the_bus)
    return _Driver(runner, the_bus)


async def _drive_guarded(driver: _Driver, run: Mapping[str, Any], action: Callable[[], Awaitable[bool]]) -> None:
    """Run `action` (a drive or an approval resume) in the run's span and end or stop the run by how it went
    (module docstring). A run that paused for an approval is left `running`."""
    run_id = str(run["_id"])
    attributes = span_attributes(org_id=current_tenant().org_id, run_id=run_id)
    paused = False
    try:
        # The run's span: every task span is its child (PRD §7.7), and it continues the request's trace.
        with continue_from(run.get("traceparent"), "orchestrator.run", attributes=attributes):
            paused = await action()
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
    if paused:
        return  # waiting for an approval: the decision resumes it
    try:
        await driver.complete(run_id)
    except (IllegalTransitionError, TransitionConflictError):
        if not await driver.is_moot(run_id):  # HR paused or blocked it while its last task finished
            raise


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

    return handle


def cleanup_run_handler(store: DataStore) -> Handler:
    """The handler for `run.cancelled`: cancel the run's pending approvals and delete the checkpoints of a
    run that is cancelled. A run that is anything else is left alone (the event names a run, it does not
    make it cancelled)."""
    runs = RunStore(store)
    saver = TenantCheckpointSaver(store)
    approvals = ApprovalStore(store)

    async def handle(job: JobContext) -> None:
        run_id = _run_id(job.payload)
        run = await runs.get(run_id)
        if run is None or run.get("status") != RunState.CANCELLED.value:
            return
        # The approvals still pending go with the run (HIL-1): their buttons and inbox rows have nothing left to decide.
        await approvals.cancel_pending(run_id, at=_utc_now())
        await saver.adelete_thread(thread_id(current_tenant().org_id, run_id))

    return handle


# --- orchestrator.resume_approval: an approval was decided (HIL-1, ADR 0020) ----------------------------

_RESUME_LOCK_TTL: Final = timedelta(seconds=120)
_APPROVAL_ID: Final = re.compile(r"appr_[A-Za-z0-9_]{1,64}")


def _approval_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("approval_id")
    if not isinstance(value, str) or not _APPROVAL_ID.fullmatch(value):
        raise ValueError("an approval.decided payload needs an approval_id")
    return value


def resume_approval_handler(
    store: DataStore,
    executor: TaskExecutor,
    clock: Callable[[], datetime] = _utc_now,
    *,
    bus: EventBus | None = None,
    policy: RunPolicy | None = None,
    drain: asyncio.Event | None = None,
    sleep: Sleep | None = None,
) -> Handler:
    """The `orchestrator.resume_approval` consumer: an approval was decided, so its run goes on.

    The decision is read from the stored approval, never from the event. Redelivery is harmless (a task
    already settled has no interrupt to resume); a decision that lands before the start job has
    checkpointed its pause, or while another approval of the run is being resumed (a per-run lease), is
    retried by the bus. A run that is not `running` (HR paused, blocked or cancelled it) is left alone: the
    decision is stored, and when HR resumes the run its driver settles the task from it. Another org's
    approval is nothing to do."""
    driver = _driver(store, executor, clock, bus, policy, drain, sleep)
    runner = driver.runner

    async def handle(job: JobContext) -> None:
        approval_id = _approval_id(job.payload)
        approval = await runner.approvals.get(approval_id)
        if approval is None or approval["status"] == "pending":
            return
        run_id = str(approval["run_id"])
        run = await runner.runs.get(run_id)
        if run is None or run.get("status") != RunState.RUNNING.value:
            return
        task = await runner.runs.task(run_id, str(approval["task_id"]))
        if task is None:
            return
        if (
            task["status"] == TaskState.RUNNING.value
            and task.get("approval_id") == approval_id
            and approval["status"] == "approved"
        ):  # an approved tool call's re-run started and was cut short: fail visibly, never ack as done
            raise TaskStepError("ResumeInterrupted", transient=True)
        if task["status"] != TaskState.WAITING_APPROVAL.value or task.get("approval_id") != approval_id:
            return  # already settled (redelivery), or not this approval's turn
        if not await runner.runs.acquire_resume(run_id, now=clock(), ttl=_RESUME_LOCK_TTL):
            raise TaskStepError("RunResuming", transient=True)
        try:
            await _drive_guarded(driver, run, lambda: driver.resume(run, approval_id))
        finally:
            await runner.runs.release_resume(run_id)

    return handle


# --- orchestrator.record_decision: a Telegram press on an approval's buttons (NOTIF-1, HIL-1) -----------


def record_decision_handler(
    store: DataStore,
    bus: EventBus | None = None,
    clock: Callable[[], datetime] = _utc_now,
    auditor: Auditor | None = None,
) -> Handler:
    """The `orchestrator.record_decision` consumer of `decision.received`.

    The press is attributed to the user the buttons were sent to: the outbox message named by the event
    carries `audience_user_id`, and its `ref` must be this approval (a press cannot decide an approval its
    button was not on). That user must be the approval's approver, and the first decision wins. A press on
    anything else, on a decided or cancelled approval, or by someone who is not the approver, changes
    nothing (it is logged by code and acknowledged: a retry would not change the answer)."""
    from onb_agents.runs import run_bus

    decider = ApprovalDecider(
        ApprovalStore(store),
        bus if bus is not None else run_bus(store),
        clock=clock,
        auditor=auditor if auditor is not None else AuditLog(store),
    )

    async def handle(job: JobContext) -> None:
        payload = job.payload
        if payload.get("ref_type") != APPROVAL_REF_TYPE:
            return
        approval_id, outbox_id = payload.get("ref_id"), payload.get("outbox_id")
        decision = _decision(payload.get("decision"))
        if not isinstance(approval_id, str) or not isinstance(outbox_id, str) or decision is None:
            _log.warning("decision.received for an approval with a malformed payload")
            return
        message = await store.tenant(OUTBOX_COLLECTION).find_one({"_id": outbox_id})
        ref = (message or {}).get("ref") or {}
        user_id = (message or {}).get("audience_user_id")
        if message is None or ref.get("type") != APPROVAL_REF_TYPE or ref.get("id") != approval_id or not user_id:
            _log.warning("decision.received for a message that is not that approval's")
            return
        user = await store.tenant("users").find_one({"_id": user_id}, projection={"role": 1})
        try:
            await decider.decide(
                approval_id,
                decision=decision,
                comment=None,
                channel="telegram",
                user_id=str(user_id),
                role=str(user["role"]) if user and user.get("role") else None,
            )
        except (AlreadyDecidedError, NotApproverError, ApprovalNotFoundError) as error:
            _log.info("telegram decision not recorded: %s", type(error).__name__)

    return handle


def _decision(value: object) -> Decision | None:
    return Decision(value) if value in {d.value for d in Decision} else None
