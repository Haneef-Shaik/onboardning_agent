"""Task and run states and the only moves between them (PRD FR-R2, FR-R3).

    pending → ready → running → (waiting_approval) → done | failed
    pending → skipped                       (compiled out: its `when` was false)
    pending | ready | running | waiting_approval → cancelled

Every write of a state goes through `check_transition`, so an illegal move (a pending task straight to
running, a finished one back to running, a skipped one revived) raises `IllegalTransitionError` and
nothing is written. A task is runnable only when every dependency is `done` or `skipped`
(`dependencies_met`): a skipped dependency never blocks.

Retrying a failed task and blocking a run (FR-R4, FR-R5) belong to RUN-2, which extends these tables.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Final

__all__ = [
    "IllegalTransitionError",
    "RunState",
    "TaskState",
    "check_run_transition",
    "check_transition",
    "dependencies_met",
]


class TaskState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL


class RunState(StrEnum):
    STARTED = "started"  # written with its tasks; the orchestrator has not picked it up yet
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_TERMINAL: Final = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.SKIPPED, TaskState.CANCELLED})
# A dependency in one of these states lets its dependents run (FR-R3).
_FINISHED: Final = frozenset({TaskState.DONE, TaskState.SKIPPED})

_TASK_MOVES: Final[Mapping[TaskState, frozenset[TaskState]]] = {
    TaskState.PENDING: frozenset({TaskState.READY, TaskState.SKIPPED, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.WAITING_APPROVAL, TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING_APPROVAL: frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.DONE: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.SKIPPED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}

_RUN_MOVES: Final[Mapping[RunState, frozenset[RunState]]] = {
    RunState.STARTED: frozenset({RunState.RUNNING, RunState.FAILED}),
    RunState.RUNNING: frozenset({RunState.COMPLETED, RunState.FAILED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """A move the state machine does not allow. Names both states, never any task data."""

    def __init__(self, kind: str, current: object, target: object) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal {kind} transition: {_name(current)} → {_name(target)}")


def _name(state: object) -> str:
    return state.value if isinstance(state, StrEnum) else repr(state)[:40]


def _parse[S: StrEnum](kind: type[S], value: object) -> S | None:
    try:
        return kind(value)
    except ValueError:
        return None


def check_transition(current: TaskState | str, target: TaskState | str) -> None:
    """Raise `IllegalTransitionError` unless a task may move from `current` to `target`."""
    now, then = _parse(TaskState, current), _parse(TaskState, target)
    if now is None or then is None or then not in _TASK_MOVES[now]:
        raise IllegalTransitionError("task", now or current, then or target)


def check_run_transition(current: RunState | str, target: RunState | str) -> None:
    """Raise `IllegalTransitionError` unless a run may move from `current` to `target`."""
    now, then = _parse(RunState, current), _parse(RunState, target)
    if now is None or then is None or then not in _RUN_MOVES[now]:
        raise IllegalTransitionError("run", now or current, then or target)


def dependencies_met(depends_on: Iterable[str], states: Mapping[str, TaskState | str]) -> bool:
    """True when every dependency is `done` or `skipped` (FR-R3). An unknown dependency blocks."""
    return all(_parse(TaskState, states.get(dependency)) in _FINISHED for dependency in depends_on)
