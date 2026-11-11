"""Run contracts: starting a run and the run it starts (PRD FR-R1, FR-R2, §10 Hires & runs; RUN-1).

`POST /runs` names a hire and a published template version. The version is pinned: the run keeps
the key, version and compiled DAG hash it started with, whatever is published later. The run's tasks
are the compiled DAG's, in template order, each with its FR-R2 state; a task pruned by its `when` is
`skipped` with a reason that names the condition, never the hire's values.

Reading, listing and controlling runs (`GET /runs`, pause, resume, cancel...) belong to RUN-2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from onb_contracts.envelope import Envelope
from onb_contracts.hires import HIRE_ID_PATTERN
from onb_contracts.inputs import RequestModel
from onb_contracts.template_lifecycle import MAX_TEMPLATE_VERSION, TEMPLATE_KEY_PATTERN

__all__ = [
    "RUN_ID_PATTERN",
    "RunCreate",
    "RunEnvelope",
    "RunRecord",
    "RunStatus",
    "RunTaskRecord",
    "TaskStatus",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

RUN_ID_PATTERN: Final = r"^run_[0-9a-f]{24}$"

TaskStatus = Literal["pending", "ready", "running", "waiting_approval", "done", "failed", "skipped", "cancelled"]
RunStatus = Literal["started", "running", "completed", "failed"]


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
