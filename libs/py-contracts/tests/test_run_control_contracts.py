"""Run control contracts (RUN-2): listing, control bodies, the task fields HR needs (PRD FR-R4, FR-R5, §10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from onb_contracts.inputs import request_model_classes
from onb_contracts.runs import (
    RunControlRequest,
    RunsEnvelope,
    RunStatus,
    RunSummary,
    RunTaskRecord,
    TaskCompleteRequest,
)
from pydantic import ValidationError


def test_the_run_statuses_include_the_control_states() -> None:
    assert set(get_args(RunStatus)) == {"started", "running", "paused", "blocked", "completed", "failed", "cancelled"}


@pytest.mark.parametrize("model", [RunControlRequest, TaskCompleteRequest])
def test_control_bodies_reject_unknown_fields_and_an_org(model: type) -> None:
    assert model in request_model_classes()
    with pytest.raises(ValidationError):
        model.model_validate({"reason": "ok", "org_id": "org_other"})


def test_pause_resume_cancel_and_retry_may_come_without_a_reason() -> None:
    assert RunControlRequest.model_validate({}).reason is None
    assert RunControlRequest.model_validate({"reason": "Holiday freeze"}).reason == "Holiday freeze"


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501, 7])
def test_a_reason_that_is_given_must_have_content_and_a_sane_length(reason: object) -> None:
    with pytest.raises(ValidationError):
        RunControlRequest.model_validate({"reason": reason})


def test_completing_a_task_by_hand_needs_a_reason() -> None:
    with pytest.raises(ValidationError):
        TaskCompleteRequest.model_validate({})
    with pytest.raises(ValidationError):
        TaskCompleteRequest.model_validate({"reason": "  "})
    assert TaskCompleteRequest.model_validate({"reason": "Done offline by IT"}).reason == "Done offline by IT"


def test_a_run_task_shows_attempts_and_the_error_type_with_defaults_for_older_callers() -> None:
    task = RunTaskRecord(
        task_id="a",
        title="A",
        type="action",
        agent="x",
        action="y",
        depends_on=[],
        status="failed",
        skip_reason=None,
        due=None,
        attempts=3,
        error="StubTaskError",
    )
    assert (task.attempts, task.error) == (3, "StubTaskError")
    bare = RunTaskRecord(
        task_id="a", title="A", type="action", agent="x", action="y", depends_on=[], status="pending",
        skip_reason=None, due=None,
    )  # fmt: skip
    assert (bare.attempts, bare.error) == (0, None)


def test_the_run_list_is_an_envelope_of_summaries_without_tasks() -> None:
    summary = RunSummary(
        id="run_" + "a" * 24,
        hire_id="h_1",
        template_key="t",
        template_version=1,
        status="blocked",
        created_by="u_hr",
        created_at=datetime(2026, 11, 12, tzinfo=UTC),
        updated_at=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert "tasks" not in RunSummary.model_fields
    assert RunsEnvelope.model_validate({"success": True, "data": [summary.model_dump()]}).data == [summary]
