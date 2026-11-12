"""Run and task records in MongoDB, through tenant repositories only (PRD §7.9 Data access).

Every call runs in the caller's tenant scope: another org's run or task is `None` / `LookupError`,
exactly like one that does not exist. State changes go through the state machines (`states.py`) and
are compare-and-set on the current status, so two writers never both move one task: the loser gets
`TransitionConflictError` and nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from onb_data import DataStore

from .records import RUN_TASKS, RUNS, RunRecords
from .states import RunState, TaskState, check_run_transition, check_transition

__all__ = ["RunStore", "TransitionConflictError"]

# The time each state stamps on a task, when it is entered.
_STAMPS: Final[Mapping[TaskState, str]] = {
    TaskState.READY: "ready_at",
    TaskState.RUNNING: "started_at",
    TaskState.DONE: "finished_at",
    TaskState.FAILED: "finished_at",
    TaskState.SKIPPED: "finished_at",
    TaskState.CANCELLED: "finished_at",
}


class TransitionConflictError(Exception):
    """The record's status changed between reading it and writing the move; nothing was written."""


class RunStore:
    def __init__(self, store: DataStore) -> None:
        self._runs = store.tenant(RUNS)
        self._tasks = store.tenant(RUN_TASKS)
        self._store = store

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(RUNS, [("org_id", 1), ("hire_id", 1)])
        await self._store.ensure_index(RUN_TASKS, [("org_id", 1), ("run_id", 1), ("task_id", 1)], unique=True)

    async def insert(self, records: RunRecords) -> None:
        """Write a run and its tasks. Call it inside the publish's `change`, so it commits with the event."""
        await self._runs.insert_one(records.run)
        await self._tasks.insert_many(list(records.tasks))

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return await self._runs.find_one({"_id": run_id})

    async def tasks(self, run_id: str) -> list[dict[str, Any]]:
        return await self._tasks.find({"run_id": run_id}, sort=[("position", 1)])

    async def task(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        return await self._tasks.find_one({"run_id": run_id, "task_id": task_id})

    async def move_task(
        self,
        run_id: str,
        task_id: str,
        target: TaskState,
        *,
        at: datetime,
        expected: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a task to `target` and return it as written.

        `expected` is the record the caller decided on (read now when not given). Raises
        `IllegalTransitionError` for a move the state machine refuses, `TransitionConflictError` when
        the status changed since `expected` was read, `LookupError` for an unknown task.
        """
        current = expected if expected is not None else await self.task(run_id, task_id)
        if current is None:
            raise LookupError("no such task in this run")
        check_transition(current["status"], target)
        changes: dict[str, Any] = {"status": target.value}
        if target in _STAMPS:
            changes[_STAMPS[target]] = at
        if target is TaskState.RUNNING:
            changes["attempts"] = int(current.get("attempts", 0)) + 1
        outcome = await self._tasks.update_one(
            {"run_id": run_id, "task_id": task_id, "status": current["status"]}, {"$set": changes}
        )
        if outcome.matched == 0:
            raise TransitionConflictError("the task changed state while this move was decided")
        return {**current, **changes}

    async def cancel_unfinished(self, run_id: str, *, at: datetime) -> list[str]:
        """Cancel every task of the run that has not finished (pending, ready, running, waiting), for a
        run that is ending. Returns the ids cancelled. A task that finishes concurrently keeps its state."""
        cancelled: list[str] = []
        for task in await self.tasks(run_id):
            if TaskState(task["status"]).terminal:
                continue
            try:
                await self.move_task(run_id, task["task_id"], TaskState.CANCELLED, at=at, expected=task)
            except TransitionConflictError:
                continue
            cancelled.append(task["task_id"])
        return cancelled

    async def move_run(self, run_id: str, target: RunState, *, at: datetime) -> dict[str, Any]:
        """Move a run to `target`; same errors as `move_task`."""
        current = await self.get(run_id)
        if current is None:
            raise LookupError("no such run")
        check_run_transition(current["status"], target)
        changes = {"status": target.value, "updated_at": at}
        outcome = await self._runs.update_one({"_id": run_id, "status": current["status"]}, {"$set": changes})
        if outcome.matched == 0:
            raise TransitionConflictError("the run changed state while this move was decided")
        return {**current, **changes}
