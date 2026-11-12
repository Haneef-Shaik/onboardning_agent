"""Run contracts (RUN-1): `POST /runs` request and the started run (PRD FR-R1, FR-R2, §10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from onb_contracts import tenant_inputs_in_model
from onb_contracts.runs import (
    RUN_ID_PATTERN,
    RunCreate,
    RunEnvelope,
    RunRecord,
    RunStatus,
    RunTaskRecord,
    TaskStatus,
)
from pydantic import ValidationError

BODY: dict[str, Any] = {
    "hire_id": "h_0123456789abcdef01234567",
    "template_key": "engineer-india",
    "template_version": 3,
}


def test_a_run_request_names_the_hire_and_the_pinned_version() -> None:
    request = RunCreate.model_validate(BODY)
    assert (request.hire_id, request.template_key, request.template_version) == (
        "h_0123456789abcdef01234567",
        "engineer-india",
        3,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"org_id": "org_globex"},
        {"hire_id": "nope"},
        {"hire_id": ""},
        {"template_key": "Bad Key"},
        {"template_key": "x" * 65},
        {"template_version": 0},
        {"template_version": "1"},
        {"template_version": 1.0},
        {"template_version": 1_000_001},
        {"template_version": None},
    ],
)
def test_a_bad_or_tenant_naming_run_request_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({**BODY, **change})


@pytest.mark.parametrize("missing", ["hire_id", "template_key", "template_version"])
def test_every_field_is_required(missing: str) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({k: v for k, v in BODY.items() if k != missing})


def test_no_run_request_field_carries_a_tenant() -> None:
    assert tenant_inputs_in_model(RunCreate) == []


def test_the_task_and_run_states_are_those_of_fr_r2() -> None:
    assert set(get_args(TaskStatus)) == {
        "pending",
        "ready",
        "running",
        "waiting_approval",
        "done",
        "failed",
        "skipped",
        "cancelled",
    }
    # FR-R2 names the task states; the run's are RUN-1's plus RUN-2's control states (FR-R4, FR-R5).
    assert set(get_args(RunStatus)) == {"started", "running", "paused", "blocked", "completed", "failed", "cancelled"}


def test_a_started_run_record_round_trips() -> None:
    record = RunRecord(
        id="run_" + "a" * 24,
        hire_id="h_1",
        template_key="engineer-india",
        template_version=1,
        dag_hash="sha256:" + "0" * 64,
        status="started",
        created_by="u_hr",
        created_at=datetime(2026, 11, 11, tzinfo=UTC),
        tasks=[
            RunTaskRecord(
                task_id="collect_docs",
                title="Collect",
                type="action",
                agent="db_agent",
                action="request_documents",
                depends_on=[],
                status="pending",
                skip_reason=None,
                due="2026-11-24",
            )
        ],
    )
    assert RunEnvelope(success=True, data=record).data == record
    assert RunRecord.model_validate(record.model_dump(mode="json")) == record


def test_the_run_id_pattern_matches_the_server_made_ids() -> None:
    import re

    assert re.fullmatch(RUN_ID_PATTERN, "run_" + "0f" * 12)
    assert not re.fullmatch(RUN_ID_PATTERN, "run_xyz")
