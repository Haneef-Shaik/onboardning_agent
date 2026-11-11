"""Agent output contracts (PRD §8.2; shapes from evals/README.md "Agent output contracts").

Every agent answers with one JSON object whose `status` is `ok | refused | needs_human`. An `ok`
answer carries its typed payload; a refusal or a hand-off to a human may leave it out and explain
itself instead. Every model forbids unknown fields, so an answer cannot smuggle extra data (an
`org_id`, a raw value) past the contract.

    contract = contract_for("template_builder", mode="suggest")   # → TemplateBuilderSuggestOutput
    answer = contract.model_validate_json(reply_text)
    refusal = contract.refusal("injection_blocked")                # what the runtime returns on a block

The agent runtime (`onb_agents.runtime`) validates the final reply against `contract_for(agent, mode)`
with one repair attempt (PRD §8.1 Output); the eval harness's `schema_valid` check uses the payload
models (`ChangeRequest`, `ApprovalPacket`, `Message`) and `Template`.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from onb_contracts.templates import Template, TemplateTask

__all__ = [
    "OUTPUT_CONTRACTS",
    "AgentOutput",
    "AgentStatus",
    "ApprovalPacket",
    "ApprovalsOutput",
    "ChangeRequest",
    "Citation",
    "DbAgentOutput",
    "Diff",
    "Message",
    "ProposedAction",
    "ProvisionedItem",
    "ProvisioningOutput",
    "RunAssistantOutput",
    "TaskUpdate",
    "TemplateBuilderDraftOutput",
    "TemplateBuilderSuggestOutput",
    "TemplatesAgentOutput",
    "contract_for",
    "contract_key",
]

AgentStatus = Literal["ok", "refused", "needs_human"]
Id = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Text = Annotated[StrictStr, Field(min_length=1, max_length=8000)]
MODE_SEPARATOR: Final = ":"
DEFAULT_MODE: Final = "draft"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


# --- payloads ---------------------------------------------------------------------------------------


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


class Citation(_Strict):
    run_id: Id
    task_id: Id


class ProvisionedItem(_Strict):
    """One integration action the provisioning agent ran (MOCK-1 tools); `reference` is the mock's id."""

    action: Id
    done: StrictBool
    reference: Id | None = None


class ProposedAction(_Strict):
    type: Id
    run_id: Id
    task_id: Id | None = None
    requires_confirmation: StrictBool


# --- outputs ----------------------------------------------------------------------------------------


class AgentOutput(_Strict):
    """The common shape: a status, and the payload `ok` requires (`PAYLOAD`)."""

    PAYLOAD: ClassVar[str]

    status: AgentStatus

    @model_validator(mode="after")
    def _ok_carries_its_payload(self) -> Self:
        if self.status == "ok" and getattr(self, self.PAYLOAD) is None:
            raise ValueError(f"an `ok` answer must carry `{self.PAYLOAD}`")
        return self

    @classmethod
    def refusal(cls, code: str) -> Self:
        """A refusal the runtime returns when a guard stops the agent; `code` is a fixed reason code."""
        return cls.model_validate(cls._refusal_fields(f"Refused by a guardrail ({code})."))

    @classmethod
    def _refusal_fields(cls, explanation: str) -> dict[str, Any]:
        return {"status": "refused", "explanation": explanation}


class TemplateBuilderDraftOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "template"

    template: Template | None = None
    explanation: Text | None = None
    unsupported_requests: list[Text] = Field(default_factory=list)


class TemplateBuilderSuggestOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "change_request"

    change_request: ChangeRequest | None = None
    explanation: Text | None = None


class ApprovalsOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "packet"

    packet: ApprovalPacket | None = None
    explanation: Text | None = None


class DbAgentOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "answer"

    answer: dict[str, Any] | None = None

    @classmethod
    def _refusal_fields(cls, explanation: str) -> dict[str, Any]:
        return {"status": "refused", "answer": {"reason": explanation}}


class TemplatesAgentOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "message"

    message: Message | None = None
    explanation: Text | None = None


class RunAssistantOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "answer"

    answer: Text | None = None
    found: StrictBool | None = None
    citations: list[Citation] = Field(default_factory=list)
    proposed_action: ProposedAction | None = None

    @classmethod
    def _refusal_fields(cls, explanation: str) -> dict[str, Any]:
        return {"status": "refused", "answer": explanation}


class ProvisioningOutput(AgentOutput):
    """Not in the evals README table (no eval cases yet): the actions run for the hire, in order."""

    PAYLOAD: ClassVar[str] = "provisioned"

    provisioned: list[ProvisionedItem] | None = None
    explanation: Text | None = None


OUTPUT_CONTRACTS: Final[dict[str, type[AgentOutput]]] = {
    "template_builder": TemplateBuilderDraftOutput,
    "template_builder:suggest": TemplateBuilderSuggestOutput,
    "approvals_agent": ApprovalsOutput,
    "db_agent": DbAgentOutput,
    "templates_agent": TemplatesAgentOutput,
    "provisioning_agent": ProvisioningOutput,
    "run_assistant": RunAssistantOutput,
}


def contract_key(agent: str, mode: str | None = None) -> str:
    """`agent`, or `agent:mode` for a mode other than the default `draft`."""
    return agent if mode in (None, DEFAULT_MODE) else f"{agent}{MODE_SEPARATOR}{mode}"


def contract_for(agent: str, mode: str | None = None) -> type[AgentOutput]:
    """The output contract of `agent` in `mode`; `KeyError` when there is none."""
    key = contract_key(agent, mode)
    if key not in OUTPUT_CONTRACTS:
        raise KeyError(f"no output contract for {key!r}")
    return OUTPUT_CONTRACTS[key]
