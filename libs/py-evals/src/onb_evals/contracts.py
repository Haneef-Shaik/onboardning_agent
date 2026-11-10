"""Output contracts `schema_valid` validates against (evals/README.md "Agent output contracts").

These are the harness's interim Pydantic models, written from the README table and the seed template
shape, because the owning goals have not shipped theirs yet: TPL-1 owns the template schema, TB-1
the change request, AGT-1/HIL-1 the approval packet and the message. When a goal ships its contract,
it replaces the entry here (`DEFAULT_CONTRACTS[target] = (path, Model)`); the check itself does not
change. Every model forbids unknown fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

__all__ = ["DEFAULT_CONTRACTS", "ApprovalPacket", "ChangeRequest", "Message", "Template"]

Id = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Text = Annotated[StrictStr, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Variable(_Strict):
    name: Id
    type: Literal["string", "enum", "date", "user_ref", "number", "boolean"]
    values: list[Text] | None = None


class PiiAccess(_Strict):
    fields: list[Id] = Field(min_length=1)
    sink: Text


class Approver(_Strict):
    rule: Literal["variable", "role", "user"]
    ref: Text


class Task(_Strict):
    id: Id
    title: Text
    agent: Id
    action: Id
    type: Literal["action", "approval"] = "action"
    depends_on: list[Id] = Field(default_factory=list)
    when: StrictStr | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    pii_access: PiiAccess | None = None
    approver: Approver | None = None
    sla_hours: StrictInt | None = Field(default=None, gt=0)
    requires_approval: StrictBool = False
    tools: list[Id] | None = None


class Template(_Strict):
    key: Id
    name: StrictStr | None = None
    version: StrictInt | None = Field(default=None, ge=1)
    status: Literal["draft", "published", "archived"]
    variables: list[Variable] = Field(default_factory=list)
    tasks: list[Task] = Field(min_length=1)


class TaskUpdate(_Strict):
    id: Id
    title: Text | None = None
    depends_on: list[Id] | None = None
    when: StrictStr | None = None
    inputs: dict[str, Any] | None = None
    sla_hours: StrictInt | None = Field(default=None, gt=0)
    requires_approval: StrictBool | None = None


class Diff(_Strict):
    add_tasks: list[Task] = Field(default_factory=list)
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
