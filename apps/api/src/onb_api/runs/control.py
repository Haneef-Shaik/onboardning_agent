"""Reading and controlling runs (PRD FR-R4, FR-R5, §3, §7.8, §10 Hires & runs; RUN-2).

**Reading.** HR and HR_ADMIN see every run of their org; a MANAGER sees only runs of hires they manage
(`manager_id == sub`), by the hire's manager at the time of the request. Another manager's run and
another org's run are 404, like one that does not exist. The org is the token's.

**Controlling** (HR and HR_ADMIN):
- `pause` (started or running → paused): the orchestrator finishes the task in hand and starts no new one.
- `resume` (paused → running) and `retry` / `complete` of a failed task of a blocked run: the orchestrator
  is told (`run.resumed`) and drives the run on from its checkpoint. A blocked run stays blocked until
  every failed task has been retried or completed, so HR is not told twice for one run.
- `cancel` (started, running, paused or blocked → cancelled): every unfinished task is cancelled and
  the orchestrator deletes the run's checkpoints (`run.cancelled`).
- `complete` marks a failed task done by hand. The reason is mandatory (the contract refuses a request
  without one, 422). Every control is audited with its reason and actor.

Each control writes the state change, the audit record and the event in one outbox transaction, so a
control without its audit record is not done (AUD-1), and the orchestrator never misses one. A request
that does not fit the run's state (pausing a paused run, retrying a task that did not fail) is 409 and
changes nothing. The event's key holds the length of the history it moves on from, so a client's retry
of the same request publishes once.

The reason is free text kept in the audit log only: it is not stored on the run or the task. It must
not carry personal data (the same rule as every audit reason; GR-1/SEC-1 own enforcing it).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_agents.runs import (
    RUN_CANCELLED,
    RUN_PAUSED,
    RUN_RESUMED,
    IllegalTransitionError,
    RunState,
    RunStore,
    TaskState,
    TransitionConflictError,
    check_run_transition,
    control_key,
    run_target,
)
from onb_contracts import ErrorCode
from onb_contracts.auth import Role
from onb_contracts.runs import RunRecord, RunSummary
from onb_data import Actor, Auditor, AuditOutcome
from onb_eventbus import EventBus

from onb_api.auth.scope import manager_filter, require_in_manager_scope
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.hires.service import HireService
from onb_api.runs.records import run_record, run_summary

__all__ = ["RunControl", "task_target"]

_RESET: Final[Mapping[str, Any]] = {"error": None, "retry_at": None}
_RUN_STATES: Final = frozenset(state.value for state in RunState)


def _is_run(document: Mapping[str, Any] | None) -> bool:
    """A run of this platform: in one of its states and pinned to a DAG (the eval seed keeps runs of its own
    shape in the same collection)."""
    return document is not None and document.get("status") in _RUN_STATES and "dag_hash" in document


def _utc_now() -> datetime:
    return datetime.now(UTC)


def task_target(run_id: str, task_id: str) -> str:
    return f"{run_target(run_id)}/task:{task_id}"


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.CONFLICT, message)


class RunControl:
    def __init__(
        self,
        *,
        runs: RunStore,
        bus: EventBus,
        auditor: Auditor,
        hires: HireService,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._runs = runs
        self._bus = bus
        self._auditor = auditor
        self._hires = hires
        self._clock = clock

    @property
    def store(self) -> RunStore:
        return self._runs

    # --- reading -------------------------------------------------------------------------------

    async def visible_run(self, principal: Principal, run_id: str) -> dict[str, Any]:
        """The run, if the caller may see it; 404 otherwise (a missing run, another org's, another manager's)."""
        run = await self._runs.get(run_id)
        if run is None or not _is_run(run):
            raise AppError(ErrorCode.NOT_FOUND)  # a document of another shape is not a run of this platform
        if principal.role is Role.MANAGER:
            hire = await self._hires.store.get(run["hire_id"])
            if hire is None:
                raise AppError(ErrorCode.NOT_FOUND)
            require_in_manager_scope(principal, hire)
        return run

    async def get(self, principal: Principal, run_id: str) -> RunRecord:
        run = await self.visible_run(principal, run_id)
        record = run_record(run, await self._runs.tasks(run_id))
        if principal.role is Role.MANAGER:
            # Traces are an HR view (PRD U7): least privilege, so the id is withheld here, not just in the UI.
            record = record.model_copy(update={"trace_id": None})
        return record

    async def list_runs(
        self, principal: Principal, *, status: RunState | None, page: int, limit: int
    ) -> tuple[list[RunSummary], int]:
        hire_ids: list[str] | None = None
        narrowing = manager_filter(principal)
        if narrowing:
            hire_ids = await self._hires.store.ids(narrowing)
        found, total = await self._runs.list_runs(
            hire_ids=hire_ids, status=status, skip=(page - 1) * limit, limit=limit
        )
        return [run_summary(run) for run in found], total

    # --- run controls --------------------------------------------------------------------------

    async def pause(self, principal: Principal, run_id: str, reason: str | None) -> RunRecord:
        return await self._move_run(principal, run_id, RunState.PAUSED, "pause", RUN_PAUSED, reason)

    async def resume(self, principal: Principal, run_id: str, reason: str | None) -> RunRecord:
        return await self._move_run(principal, run_id, RunState.RUNNING, "resume", RUN_RESUMED, reason)

    async def cancel(self, principal: Principal, run_id: str, reason: str | None) -> RunRecord:
        return await self._move_run(principal, run_id, RunState.CANCELLED, "cancel", RUN_CANCELLED, reason)

    async def _move_run(
        self,
        principal: Principal,
        run_id: str,
        target: RunState,
        verb: str,
        event: str,
        reason: str | None,
    ) -> RunRecord:
        run = await self._existing(run_id)
        if target is RunState.RUNNING and run["status"] != RunState.PAUSED.value:
            # `started → running` and `blocked → running` are the orchestrator's and the task controls'
            # moves: only a paused run is resumed with this.
            raise _conflict("Only a paused run can be resumed")
        try:
            check_run_transition(run["status"], target)
        except IllegalTransitionError:
            raise _conflict(f"A {run['status']} run cannot be moved to {target.value}") from None
        expected = RunState(run["status"])

        async def change() -> None:
            now = self._clock()
            await self._runs.move_run(run_id, target, at=now, by=principal.user_id, expected_status=expected)
            if target is RunState.CANCELLED:
                await self._runs.cancel_unfinished(run_id, at=now)
            await self._audit(principal, f"run.{verb}", run_target(run_id), reason)

        await self._publish(event, run_id, control_key(run_id, verb, len(run.get("history", ()))), change)
        return await self._record(run_id)

    # --- task controls -------------------------------------------------------------------------

    async def retry_task(self, principal: Principal, run_id: str, task_id: str, reason: str | None) -> RunRecord:
        """A failed task of a blocked run goes back to `ready` with a fresh set of attempts."""
        fields = {**_RESET, "attempts": 0}
        return await self._move_task(principal, run_id, task_id, TaskState.READY, "retry", fields, reason)

    async def complete_task(self, principal: Principal, run_id: str, task_id: str, reason: str) -> RunRecord:
        """HR says a failed task is done, and why; the run goes on as if it had finished."""
        return await self._move_task(principal, run_id, task_id, TaskState.DONE, "complete", dict(_RESET), reason)

    async def _move_task(
        self,
        principal: Principal,
        run_id: str,
        task_id: str,
        target: TaskState,
        verb: str,
        fields: Mapping[str, Any],
        reason: str | None,
    ) -> RunRecord:
        run = await self._existing(run_id)
        task = await self._runs.task(run_id, task_id)
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND)
        if run["status"] != RunState.BLOCKED.value:
            raise _conflict("Only a blocked run's tasks can be retried or completed")
        if task["status"] != TaskState.FAILED.value:
            raise _conflict("Only a failed task can be retried or completed")
        event = RUN_RESUMED  # always: a resume for a run that stays blocked is a no-op for the orchestrator
        key = control_key(run_id, f"task_{verb}:{task_id}", len(task.get("history", ())))

        async def change() -> None:
            now = self._clock()
            await self._runs.move_task(
                run_id, task_id, target, at=now, expected=task, by=principal.user_id, fields=fields
            )
            # Decided here, in the transaction, after this task's move: two HR users clearing the last two
            # failed tasks at once each see the other's move, and one of them unblocks the run.
            others = [t for t in await self._runs.tasks(run_id) if t["status"] == "failed"]
            if not others:
                await self._runs.move_run(
                    run_id, RunState.RUNNING, at=now, by=principal.user_id, expected_status=RunState.BLOCKED
                )
            await self._audit(principal, f"run.task.{verb}", task_target(run_id, task_id), reason)

        await self._publish(event, run_id, key, change)
        return await self._record(run_id)

    # --- shared --------------------------------------------------------------------------------

    async def _existing(self, run_id: str) -> dict[str, Any]:
        run = await self._runs.get(run_id)
        if run is None or not _is_run(run):
            raise AppError(ErrorCode.NOT_FOUND)
        return run

    async def _record(self, run_id: str) -> RunRecord:
        run = await self._existing(run_id)
        return run_record(run, await self._runs.tasks(run_id))

    async def _audit(self, principal: Principal, action: str, target: str, reason: str | None) -> None:
        await self._auditor.audit(
            actor=Actor.user(principal.user_id),
            action=action,
            target=target,
            outcome=AuditOutcome.SUCCESS,
            reason=reason,
        )

    async def _publish(self, event: str, run_id: str, key: str, change: Callable[[], Any]) -> None:
        try:
            await self._bus.publish(event, {"run_id": run_id}, idempotency_key=key, run_id=run_id, change=change)
        except (IllegalTransitionError, TransitionConflictError):
            raise _conflict("The run or task changed while this was decided; read it and try again") from None
