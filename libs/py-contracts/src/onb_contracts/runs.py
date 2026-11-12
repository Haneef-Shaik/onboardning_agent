"""Run contracts: starting a run and the run it starts (PRD FR-R1, FR-R2, §10 Hires & runs; RUN-1).

`POST /runs` names a hire and a published template version. The version is pinned: the run keeps
the key, version and compiled DAG hash it started with, whatever is published later. The run's tasks
are the compiled DAG's, in template order, each with its FR-R2 state; a task pruned by its `when` is
`skipped` with a reason that names the condition, never the hire's values.

RUN-2 adds reading and controlling runs: `GET /runs` (`RunSummary` per run, no tasks), `GET /runs/{id}`
(`RunRecord`, tasks with their attempts and error type), `POST /runs/{id}/{pause|resume|cancel}` and
`POST /runs/{id}/tasks/{task_id}/{retry|complete}`. A reason is optional for pause, resume, cancel and
retry and mandatory for completing a task by hand (FR-R5); it is audited, never stored on the run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from onb_contracts.envelope import Envelope
from onb_contracts.hires import HIRE_ID_PATTERN, MAX_REASON_LENGTH, REASON_PATTERN
from onb_contracts.inputs import RequestModel
from onb_contracts.template_lifecycle import MAX_TEMPLATE_VERSION, TEMPLATE_KEY_PATTERN

__all__ = [
    "RUN_ID_PATTERN",
    "TASK_ID_PATTERN",
    "RunControlRequest",
    "RunCreate",
    "RunEnvelope",
    "RunRecord",
    "RunStatus",
    "RunSummary",
    "RunTaskRecord",
    "RunsEnvelope",
    "TaskCompleteRequest",
    "TaskStatus",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

RUN_ID_PATTERN: Final = r"^run_[0-9a-f]{24}$"
TASK_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"

TaskStatus = Literal["pending", "ready", "running", "waiting_approval", "done", "failed", "skipped", "cancelled"]
RunStatus = Literal["started", "running", "paused", "blocked", "completed", "failed", "cancelled"]


class RunCreate(RequestModel):
    """`POST /runs`: start a run for a hire on a published template version (FR-R1)."""

    hire_id: Annotated[str, Field(max_length=66, pattern=HIRE_ID_PATTERN)]
    template_key: Annotated[str, Field(max_length=64, pattern=TEMPLATE_KEY_PATTERN)]
    template_version: Annotated[StrictInt, Field(ge=1, le=MAX_TEMPLATE_VERSION)]


class RunTaskRecord(BaseModel):
    model_config = _FROZEN

    task_id: str
    title: str
    type: Literal["action", "approval"]
    agent: str
    action: str
    depends_on: list[str]
    status: TaskStatus
    skip_reason: str | None
    due: str | None
    attempts: int = 0
    error: str | None = None
    """The error type of the last failed attempt (`StubTaskError`, `LeaseExpired`), never a message."""


class RunRecord(BaseModel):
    """A run, pinned to the template version and DAG hash it was compiled from."""

    model_config = _FROZEN

    id: str
    hire_id: str
    template_key: str
    template_version: int
    dag_hash: str
    status: RunStatus
    created_by: str
    created_at: datetime
    tasks: list[RunTaskRecord]


class RunEnvelope(Envelope[RunRecord]):
    """A run that was just started."""


class RunSummary(BaseModel):
    """A run in a list: no tasks (`GET /runs/{id}` has them)."""

    model_config = _FROZEN

    id: str
    hire_id: str
    template_key: str
    template_version: int
    status: RunStatus
    created_by: str
    created_at: datetime
    updated_at: datetime


class RunsEnvelope(Envelope[list[RunSummary]]):
    """One page of runs, newest first; `meta.page` has the total."""


class RunControlRequest(RequestModel):
    """`POST /runs/{id}/{pause|resume|cancel}` and `.../tasks/{task_id}/retry`: a reason is optional."""

    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)] | None = None


class TaskCompleteRequest(RequestModel):
    """`POST /runs/{id}/tasks/{task_id}/complete`: HR says the task is done and why (FR-R5)."""

    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)]
