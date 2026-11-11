"""Template lifecycle contracts: drafts, validation reports and versions (PRD FR-T3, FR-T4, §10 Templates).

The lifecycle is `draft → published → archived`:
- a draft (`template_drafts`) is edited with `PUT /templates/drafts/{id}`, which validates on save
  (FR-T3) and stores the draft with the issues found. A draft with a sensitive-field issue is not
  stored at all, so raw PII never lands in a draft;
- `POST /templates/drafts/{id}/publish` (HR_ADMIN only) validates again against the current
  registry and, when valid, appends the next version of the template's key (`template_versions`,
  append-only: a version never changes). The draft is then locked;
- `POST /templates/{key}/versions/{version}/archive` (HR_ADMIN only) archives a version. The version
  record keeps its content; only its status changes.

`version` and `status` inside a template body are lifecycle-owned: a draft body's values are
ignored, and a version's body carries the stored version number and current status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from onb_contracts.envelope import Envelope
from onb_contracts.templates import Template

__all__ = [
    "DRAFT_ID_PATTERN",
    "MAX_TEMPLATE_VERSION",
    "TEMPLATE_KEY_PATTERN",
    "DraftCreate",
    "DraftEnvelope",
    "DraftRecord",
    "DraftStatus",
    "DraftUpdate",
    "DraftsEnvelope",
    "TemplateIssue",
    "TemplateSummariesEnvelope",
    "TemplateSummary",
    "TemplateValidation",
    "TemplateValidationEnvelope",
    "TemplateVersionEnvelope",
    "TemplateVersionRecord",
    "TemplateVersionSummary",
    "VersionStatus",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

DRAFT_ID_PATTERN: Final = r"^tdr_[0-9a-f]{32}$"
TEMPLATE_KEY_PATTERN: Final = r"^[a-z][a-z0-9_-]{0,63}$"
MAX_TEMPLATE_VERSION: Final = 1_000_000

DraftStatus = Literal["draft", "published"]
VersionStatus = Literal["published", "archived"]
Revision = Annotated[StrictInt, Field(ge=1)]


class DraftCreate(BaseModel):
    """`POST /templates/drafts`: a new draft from a template body."""

    model_config = _FROZEN

    template: Template


class DraftUpdate(BaseModel):
    """`PUT /templates/drafts/{id}`: the whole new body. With `revision`, the save applies only to that
    revision (409 otherwise), so two editors cannot overwrite each other unseen."""

    model_config = _FROZEN

    template: Template
    revision: Revision | None = None


class TemplateIssue(BaseModel):
    """One FR-T3 problem: the rule, the task it is about (or none) and a message without raw values."""

    model_config = _FROZEN

    rule: str
    task_id: str | None
    message: str


class TemplateValidation(BaseModel):
    """`POST /templates/drafts/{id}/validate`: the report against the current registry."""

    model_config = _FROZEN

    ok: bool
    issues: list[TemplateIssue]
    order: list[str]
    """The tasks in a deterministic topological order; empty when the graph has a cycle."""


class DraftRecord(BaseModel):
    model_config = _FROZEN

    id: str
    key: str
    revision: int
    status: DraftStatus
    template: Template
    issues: list[TemplateIssue]
    """What validation found when the draft was last saved; publishing validates again."""
    published_version: int | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class TemplateVersionSummary(BaseModel):
    model_config = _FROZEN

    version: int
    status: VersionStatus
    published_at: datetime
    published_by: str
    archived_at: datetime | None
    archived_by: str | None


class TemplateVersionRecord(TemplateVersionSummary):
    """One published version: its key, its immutable body and where it came from."""

    key: str
    template: Template
    draft_id: str


class TemplateSummary(BaseModel):
    """`GET /templates`: one row per template key, with every version's status."""

    model_config = _FROZEN

    key: str
    name: str | None
    latest_version: int
    versions: list[TemplateVersionSummary]


class DraftEnvelope(Envelope[DraftRecord]):
    pass


class DraftsEnvelope(Envelope[list[DraftRecord]]):
    pass


class TemplateValidationEnvelope(Envelope[TemplateValidation]):
    pass


class TemplateVersionEnvelope(Envelope[TemplateVersionRecord]):
    pass


class TemplateSummariesEnvelope(Envelope[list[TemplateSummary]]):
    pass
