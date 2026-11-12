"""Run and task records in MongoDB, through tenant repositories only (PRD §7.9 Data access).

Every call runs in the caller's tenant scope: another org's run or task is `None` / `LookupError`,
exactly like one that does not exist. State changes go through the state machines (`states.py`) and
are compare-and-set on the current status, so two writers never both move one task: the loser gets
`TransitionConflictError` and nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from onb_data import DataStore

from .records import RUN_TASKS, RUNS, RunRecords
from .states import RunState, TaskState, check_run_transition, check_transition

__all__ = ["RunStore", "TransitionConflictError", "lease_expired"]

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


def lease_expired(task: Mapping[str, Any], now: datetime) -> bool:
    """True when nobody holds the task's lease: it ran out, was released, or was never set."""
    until = task.get("lease_until")
    return until is None or bool(until <= now)


def _history(
    current: Mapping[str, Any], source: str, target: str, at: datetime, by: str | None, *, note: str | None = None
) -> list[dict[str, Any]]:
    entry: dict[str, Any] = {"from": source, "to": target, "at": at, "by": by}
    if note is not None:
        entry["note"] = note
    return [*current.get("history", ()), entry]


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

    async def list_runs(
        self,
        *,
        hire_ids: Sequence[str] | None = None,
        status: RunState | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the org's runs, newest first, and how many match. `hire_ids` narrows to those hires
        (an empty list matches nothing: a manager with no reports sees no runs)."""
        if hire_ids is not None and not hire_ids:
            return [], 0
        # Only runs of this platform (in one of its states, pinned to a DAG): the collection may also hold
        # fixture documents of another shape (the eval harness's seed), which are not runs of this platform.
        query: dict[str, Any] = {"status": {"$in": [state.value for state in RunState]}, "dag_hash": {"$exists": True}}
        if hire_ids is not None:
            query["hire_id"] = {"$in": list(hire_ids)}
        if status is not None:
            query["status"] = status.value
        page = await self._runs.find(query, sort=[("created_at", -1), ("_id", -1)], skip=skip, limit=limit)
        return page, await self._runs.count(query)

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
        by: str | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a task to `target` and return it as written.

        `expected` is the record the caller decided on (read now when not given). `fields` are written
        with the move (the lease, the error type, a reset attempt count) and win over the defaults. Each
        move is appended to the task's `history` (`from`, `to`, `at`, `by`) in the same write, which is
        what the live stream replays. Leaving `running` releases the lease. Raises
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
        else:
            changes.update({"lease_token": None, "lease_until": None})
        changes.update(fields or {})
        changes["history"] = _history(current, current["status"], target.value, at, by)
        guard: dict[str, Any] = {"run_id": run_id, "task_id": task_id, "status": current["status"]}
        if current["status"] == TaskState.RUNNING.value and "lease_token" in current:
            # A running task is moved only by the holder of its lease: a worker that was taken over
            # cannot finish or fail the task the new holder is running.
            guard["lease_token"] = current["lease_token"]
        outcome = await self._tasks.update_one(guard, {"$set": changes})
        if outcome.matched == 0:
            raise TransitionConflictError("the task changed state while this move was decided")
        return {**current, **changes}

    async def record_model(self, run_id: str, task_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
        """Record the model a task started with, once, and return what is recorded (PRD §7.1: running
        tasks keep the model they started with). A task that already has one keeps it: the stored record
        is returned and nothing is written."""
        await self._tasks.update_one(
            {"run_id": run_id, "task_id": task_id, "llm": {"$exists": False}}, {"$set": {"llm": dict(record)}}
        )
        stored = await self.task(run_id, task_id)
        if stored is None:
            raise LookupError("no such task in this run")
        recorded: dict[str, Any] = stored["llm"]
        return recorded

    async def fail_running(
        self, run_id: str, task_id: str, *, failure: Mapping[str, str], at: datetime, by: str | None = None
    ) -> bool:
        """Fail a task and record why, in one update that applies only while the task is `running`.
        Returns False (writing nothing) for a task in any other state, so a task that finished meanwhile
        never gets a failure stamped on it. Like every task move it releases the lease and is appended to
        the task's history (the live stream shows it), and it applies only to the lease holder that was
        read, so a takeover in between wins."""
        check_transition(TaskState.RUNNING, TaskState.FAILED)
        current = await self.task(run_id, task_id)
        if current is None or current["status"] != TaskState.RUNNING.value:
            return False
        changes: dict[str, Any] = {
            "status": TaskState.FAILED.value,
            "finished_at": at,
            "failure": dict(failure),
            "error": failure.get("kind"),  # the cause code HR sees on the task (never a message)
            "lease_token": None,
            "lease_until": None,
            "retry_at": None,
            "history": _history(current, "running", "failed", at, by, note="failed_from_outside"),
        }
        outcome = await self._tasks.update_one(
            {
                "run_id": run_id,
                "task_id": task_id,
                "status": TaskState.RUNNING.value,
                "lease_token": current.get("lease_token"),
            },
            {"$set": changes},
        )
        return outcome.matched == 1

    async def record_outcome(
        self,
        run_id: str,
        task_id: str,
        *,
        result: Mapping[str, Any] | None,
        failure: Mapping[str, str] | None,
        only_if: TaskState | None = None,
    ) -> None:
        """Store what an agent task produced (`result`; left as it is when None) and why it failed, if it did
        (`failure = {kind, reason}`: codes and counts, never message text). Call it before the state move,
        so a failed task shows its cause. With `only_if`, nothing is written unless the task is in that state
        (a task moved from outside, e.g. failed by a sink, keeps what that writer recorded)."""
        changes: dict[str, Any] = {}
        if failure is not None:  # never clear a cause another writer recorded (a sink's rejection)
            changes["failure"] = dict(failure)
        if result is not None:
            changes["result"] = dict(result)
        if not changes:
            return
        match: dict[str, Any] = {"run_id": run_id, "task_id": task_id}
        if only_if is not None:
            match["status"] = only_if.value
        outcome = await self._tasks.update_one(match, {"$set": changes})
        if outcome.matched == 0 and await self.task(run_id, task_id) is None:
            raise LookupError("no such task in this run")

    async def run_tokens(self, run_id: str) -> int:
        """The tokens the run's tasks have recorded so far (their `result.usage.tokens`)."""
        total = 0
        for task in await self.tasks(run_id):
            usage = (task.get("result") or {}).get("usage") or {}
            total += int(usage.get("tokens", 0))
        return total

    async def take_over(
        self,
        run_id: str,
        task_id: str,
        *,
        expected: Mapping[str, Any],
        token: str,
        lease_until: datetime,
        at: datetime,
    ) -> dict[str, Any]:
        """Take a `running` task whose holder is gone: new lease, one more attempt, recorded in its history.

        Compare-and-set on the lease token the caller saw, so of two workers taking over one task only
        one wins; the other gets `TransitionConflictError`.
        """
        changes: dict[str, Any] = {
            "lease_token": token,
            "lease_until": lease_until,
            "attempts": int(expected.get("attempts", 0)) + 1,
            "started_at": at,
        }
        changes["history"] = _history(expected, "running", "running", at, None, note="takeover")
        outcome = await self._tasks.update_one(
            {"run_id": run_id, "task_id": task_id, "status": "running", "lease_token": expected.get("lease_token")},
            {"$set": changes},
        )
        if outcome.matched == 0:
            raise TransitionConflictError("the task was taken over, finished or released while this was decided")
        return {**expected, **changes}

    async def renew_lease(self, run_id: str, task_id: str, token: str, lease_until: datetime) -> bool:
        """Extend the lease the caller holds. False when it no longer holds it (so it must stop)."""
        outcome = await self._tasks.update_one(
            {"run_id": run_id, "task_id": task_id, "status": "running", "lease_token": token},
            {"$set": {"lease_until": lease_until}},
        )
        return outcome.matched > 0

    async def release_lease(self, run_id: str, task_id: str, token: str, *, attempts: int | None = None) -> bool:
        """Let go of the lease the caller holds, so another worker may take the task over at once.
        `attempts` is the count to leave behind: a task cut off through no fault of its own gives the
        attempt back, and the takeover that follows counts it again."""
        changes: dict[str, Any] = {"lease_until": None, "lease_token": None}
        if attempts is not None:
            changes["attempts"] = attempts
        outcome = await self._tasks.update_one(
            {"run_id": run_id, "task_id": task_id, "status": "running", "lease_token": token}, {"$set": changes}
        )
        return outcome.matched > 0

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

    async def move_run(
        self,
        run_id: str,
        target: RunState,
        *,
        at: datetime,
        by: str | None = None,
        expected_status: RunState | None = None,
    ) -> dict[str, Any]:
        """Move a run to `target`; same errors as `move_task`. `expected_status` makes the move apply only
        when the run is in that state (`TransitionConflictError` otherwise). Appended to the run's `history`."""
        current = await self.get(run_id)
        if current is None:
            raise LookupError("no such run")
        if expected_status is not None and current["status"] != expected_status.value:
            raise TransitionConflictError("the run is not in the state this move expects")
        check_run_transition(current["status"], target)
        changes = {
            "status": target.value,
            "updated_at": at,
            "history": _history(current, current["status"], target.value, at, by),
        }
        outcome = await self._runs.update_one({"_id": run_id, "status": current["status"]}, {"$set": changes})
        if outcome.matched == 0:
            raise TransitionConflictError("the run changed state while this move was decided")
        return {**current, **changes}
