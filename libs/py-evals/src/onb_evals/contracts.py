"""Output contracts `schema_valid` validates against (evals/README.md "Agent output contracts").

`template` is the real schema, `onb_contracts.templates.Template` (TPL-1), and a change request adds
tasks of the real `TemplateTask` shape. The rest are the harness's interim Pydantic models, written
from the README table, because the owning goals have not shipped theirs yet: TB-1 the change request,
AGT-1/HIL-1 the approval packet and the message. When a goal ships its contract, it replaces the
entry here (`DEFAULT_CONTRACTS[target] = (path, Model)`); the check itself does not change. Every
model forbids unknown fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from onb_contracts.templates import Template, TemplateTask
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

__all__ = ["DEFAULT_CONTRACTS", "ApprovalPacket", "ChangeRequest", "Message", "Template"]

Id = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Text = Annotated[StrictStr, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskUpdate(_Strict):
    id: Id
    title: Text | None = None
    depends_on: list[Id] | None = None
    when: StrictStr | None = None
    inputs: dict[str, Any] | None = None
    sla_hours: StrictInt | None = Field(default=None, gt=0)
    requires_approval: StrictBool | None = None


class Diff(_Strict):
    add_tasks: list[TemplateTask] = Field(default_factory=list)
    update_tasks: list[TaskUpdate] = Field(default_factory=list)
    remove_tasks: list[Id] = Field(default_factory=list)


class ChangeRequest(_Strict):
    template_key: Id
    base_version: StrictInt = Field(ge=1)
    diff: Diff
    rationale: Text
    risk_flags: list[Id] = Field(default_factory=list)


class ApprovalPacket(_Strict):
    summary: Text
    recommendation: Literal["approve", "reject", "needs_human"]
    risk_flags: list[Id] = Field(default_factory=list)
    cited_policies: list[Id] = Field(default_factory=list)


class Message(_Strict):
    channel: Literal["telegram", "email"]
    recipient_token: Text
    body: Text


DEFAULT_CONTRACTS: dict[str, tuple[str, type[BaseModel]]] = {
    "template": ("template", Template),
    "change_request": ("change_request", ChangeRequest),
    "approval_packet": ("packet", ApprovalPacket),
    "message": ("message", Message),
}
