"""Agent output contracts (PRD §8.2; shapes from evals/README.md "Agent output contracts").

Every agent answers with one JSON object whose `status` is `ok | refused | needs_human`. An `ok`
answer carries its typed payload; a refusal or a hand-off to a human may leave it out and explain
itself instead. Every model forbids unknown fields; free-form JSON (a DB answer, task `inputs`) is
bounded in depth and size, and a DB answer never carries an `org_id`. A recipient is a `ptk_` token.

    contract = contract_for("template_builder", mode="suggest")   # → TemplateBuilderSuggestOutput
    answer = contract.model_validate_json(reply_text)
    refusal = contract.refusal("injection_blocked")                # what the runtime returns on a block

The agent runtime (`onb_agents.runtime`) validates the final reply against `contract_for(agent, mode)`
with one repair attempt (PRD §8.1 Output); the eval harness's `schema_valid` check uses the payload
models (`ChangeRequest`, `ApprovalPacket`, `Message`) and `Template`.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from onb_contracts.templates import (
    MAX_INPUT_DEPTH,
    MAX_INPUTS_SIZE,
    MAX_LIST,
    Identifier,
    InputKey,
    Template,
    TemplateTask,
    Title,
    bounded_json,
)

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
# A PII token (PRD §7.10): never a raw value. Seed tokens (`ptk_seed_…`) match too.
PiiToken = Annotated[StrictStr, Field(pattern=r"^ptk_[A-Za-z0-9_]{4,160}$")]
# Keys an answer may never carry at any depth: the org comes from the token, never from an agent.
RESERVED_KEYS: Final = frozenset({"org_id"})
MODE_SEPARATOR: Final = ":"
DEFAULT_MODE: Final = "draft"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


# --- payloads ---------------------------------------------------------------------------------------


def _reserved_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(k) for k in value if k in RESERVED_KEYS} | {k for v in value.values() for k in _reserved_keys(v)}
    if isinstance(value, list):
        return {k for item in value for k in _reserved_keys(item)}
    return set()


def _bounded_answer(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    reserved = sorted(_reserved_keys(value))
    if reserved:
        raise ValueError(f"an answer may not carry {', '.join(reserved)}")
    return bounded_json(value, depth=MAX_INPUT_DEPTH, size=MAX_INPUTS_SIZE)


class TaskUpdate(_Strict):
    """A change to one existing task: the same field types and bounds as `TemplateTask`."""

    id: Identifier
    title: Title | None = None
    depends_on: tuple[Identifier, ...] | None = Field(default=None, max_length=MAX_LIST)
    when: Annotated[StrictStr, Field(min_length=1, max_length=512)] | None = None
    inputs: dict[InputKey, JsonValue] | None = None
    sla_hours: Annotated[StrictInt, Field(gt=0, le=24 * 365)] | None = None
    requires_approval: StrictBool | None = None

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("depends_on repeats a task")
        return value

    @field_validator("inputs")
    @classmethod
    def _bounded_inputs(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return None if value is None else bounded_json(value, depth=MAX_INPUT_DEPTH, size=MAX_INPUTS_SIZE)


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
    recipient_token: PiiToken
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
    """The common shape: a status, the payload `ok` requires (`PAYLOAD`), and the field a refusal or a
    hand-off explains itself in (`EXPLAINED_BY`). A refusal never carries the payload (unless the payload
    is itself where the agent explains, as for the DB agent and the Run Assistant)."""

    PAYLOAD: ClassVar[str]
    EXPLAINED_BY: ClassVar[str] = "explanation"

    status: AgentStatus

    @model_validator(mode="after")
    def _payload_matches_the_status(self) -> Self:
        payload = getattr(self, self.PAYLOAD)
        if self.status == "ok":
            if payload is None:
                raise ValueError(f"an `ok` answer must carry `{self.PAYLOAD}`")
            return self
        if getattr(self, self.EXPLAINED_BY) is None:
            raise ValueError(f"a `{self.status}` answer must explain itself in `{self.EXPLAINED_BY}`")
        if self.status == "refused" and self.PAYLOAD != self.EXPLAINED_BY and payload is not None:
            raise ValueError(f"a refusal carries no `{self.PAYLOAD}`")
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
    EXPLAINED_BY: ClassVar[str] = "answer"

    answer: dict[str, JsonValue] | None = None

    @field_validator("answer")
    @classmethod
    def _bounded(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return None if value is None else _bounded_answer(value)

    @classmethod
    def _refusal_fields(cls, explanation: str) -> dict[str, Any]:
        return {"status": "refused", "answer": {"reason": explanation}}


class TemplatesAgentOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "message"

    message: Message | None = None
    explanation: Text | None = None


class RunAssistantOutput(AgentOutput):
    PAYLOAD: ClassVar[str] = "answer"
    EXPLAINED_BY: ClassVar[str] = "answer"

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
