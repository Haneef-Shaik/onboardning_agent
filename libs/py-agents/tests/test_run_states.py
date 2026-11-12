"""The task and run state machines (PRD FR-R2, FR-R3; RUN-1)."""

from __future__ import annotations

import pytest
from onb_agents.runs import (
    IllegalTransitionError,
    RunState,
    TaskState,
    check_run_transition,
    check_transition,
    dependencies_met,
)

LEGAL = [
    (TaskState.PENDING, TaskState.READY),
    (TaskState.PENDING, TaskState.SKIPPED),
    (TaskState.PENDING, TaskState.CANCELLED),
    (TaskState.READY, TaskState.RUNNING),
    (TaskState.READY, TaskState.CANCELLED),
    (TaskState.RUNNING, TaskState.WAITING_APPROVAL),
    (TaskState.RUNNING, TaskState.DONE),
    (TaskState.RUNNING, TaskState.FAILED),
    (TaskState.RUNNING, TaskState.CANCELLED),
    (TaskState.WAITING_APPROVAL, TaskState.RUNNING),  # HIL-1: an approved tool call runs the task again
    (TaskState.WAITING_APPROVAL, TaskState.DONE),
    (TaskState.WAITING_APPROVAL, TaskState.FAILED),
    (TaskState.WAITING_APPROVAL, TaskState.CANCELLED),
    # RUN-2 (FR-R4, FR-R5): a failed task is retried (automatically or by HR) or completed by hand.
    (TaskState.FAILED, TaskState.READY),
    (TaskState.FAILED, TaskState.DONE),
]


@pytest.mark.parametrize(("current", "target"), LEGAL)
def test_each_transition_of_fr_r2_is_allowed(current: TaskState, target: TaskState) -> None:
    check_transition(current, target)


ILLEGAL = [(current, target) for current in TaskState for target in TaskState if (current, target) not in LEGAL]


@pytest.mark.parametrize(("current", "target"), ILLEGAL)
def test_illegal_state_transitions_are_rejected(current: TaskState, target: TaskState) -> None:
    with pytest.raises(IllegalTransitionError) as raised:
        check_transition(current, target)
    assert raised.value.current is current
    assert raised.value.target is target
    assert current.value in str(raised.value)
    assert target.value in str(raised.value)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskState.PENDING, TaskState.RUNNING),  # never runs without being ready
        (TaskState.PENDING, TaskState.DONE),
        (TaskState.DONE, TaskState.RUNNING),  # a finished task never runs again
        (TaskState.SKIPPED, TaskState.READY),  # a pruned task never comes back
        (TaskState.CANCELLED, TaskState.READY),
        (TaskState.FAILED, TaskState.RUNNING),  # a retry goes through `ready`
        (TaskState.FAILED, TaskState.SKIPPED),
        (TaskState.RUNNING, TaskState.RUNNING),
    ],
)
def test_the_transitions_that_matter_most_are_illegal(current: TaskState, target: TaskState) -> None:
    with pytest.raises(IllegalTransitionError):
        check_transition(current, target)


def test_unknown_states_are_rejected() -> None:
    with pytest.raises(IllegalTransitionError):
        check_transition("pending", "nonsense")
    with pytest.raises(IllegalTransitionError):
        check_transition("nonsense", "ready")


def test_states_given_as_strings_are_checked_like_enums() -> None:
    check_transition("pending", "ready")
    with pytest.raises(IllegalTransitionError):
        check_transition("done", "running")


def test_terminal_task_states() -> None:
    assert {s for s in TaskState if s.terminal} == {
        TaskState.DONE,
        TaskState.FAILED,
        TaskState.SKIPPED,
        TaskState.CANCELLED,
    }
    # `failed` is terminal for the run's progress but HR may retry or complete it (RUN-2).
    assert {s for s in TaskState if s.resolvable} == {TaskState.FAILED}


def test_skipped_dependencies_do_not_block() -> None:
    states = {"a": TaskState.SKIPPED, "b": TaskState.DONE}
    assert dependencies_met(("a", "b"), states)
    assert dependencies_met(("a",), states)
    assert dependencies_met((), states)


@pytest.mark.parametrize(
    "blocking",
    [
        TaskState.PENDING,
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.WAITING_APPROVAL,
        TaskState.FAILED,
        TaskState.CANCELLED,
    ],
)
def test_a_dependency_that_is_not_done_or_skipped_blocks(blocking: TaskState) -> None:
    assert not dependencies_met(("a", "b"), {"a": TaskState.DONE, "b": blocking})


def test_an_unknown_dependency_blocks() -> None:
    assert not dependencies_met(("ghost",), {"a": TaskState.DONE})


RUN_LEGAL = [
    (RunState.STARTED, RunState.RUNNING),
    (RunState.STARTED, RunState.FAILED),
    (RunState.RUNNING, RunState.COMPLETED),
    (RunState.RUNNING, RunState.FAILED),
    # RUN-2: control and blocking (FR-R4, FR-R5)
    (RunState.STARTED, RunState.PAUSED),
    (RunState.STARTED, RunState.CANCELLED),
    (RunState.RUNNING, RunState.PAUSED),
    (RunState.RUNNING, RunState.BLOCKED),
    (RunState.RUNNING, RunState.CANCELLED),
    (RunState.PAUSED, RunState.RUNNING),
    (RunState.PAUSED, RunState.CANCELLED),
    (RunState.BLOCKED, RunState.RUNNING),
    (RunState.BLOCKED, RunState.CANCELLED),
]


def test_a_finished_run_never_moves_again() -> None:
    for finished in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
        for target in RunState:
            with pytest.raises(IllegalTransitionError):
                check_run_transition(finished, target)


def test_which_run_states_are_active() -> None:
    assert {s for s in RunState if s.active} == {
        RunState.STARTED,
        RunState.RUNNING,
        RunState.PAUSED,
        RunState.BLOCKED,
    }


@pytest.mark.parametrize(("current", "target"), RUN_LEGAL)
def test_run_transitions_are_allowed(current: RunState, target: RunState) -> None:
    check_run_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [(c, t) for c in RunState for t in RunState if (c, t) not in RUN_LEGAL],
)
def test_illegal_run_transitions_are_rejected(current: RunState, target: RunState) -> None:
    with pytest.raises(IllegalTransitionError):
        check_run_transition(current, target)


def test_the_states_are_the_api_contracts_states() -> None:
    from typing import get_args

    from onb_contracts.runs import RunStatus, TaskStatus

    assert {s.value for s in TaskState} == set(get_args(TaskStatus))
    assert {s.value for s in RunState} == set(get_args(RunStatus))
