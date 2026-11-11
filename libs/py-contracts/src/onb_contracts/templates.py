"""The onboarding template schema (PRD §6.1 template shape, §5.1, FR-T1, FR-T3).

    key: engineer-india
    variables:
      - { name: role, type: enum, values: [engineer, designer, sales] }
      - { name: start_date, type: date }
      - { name: manager_id, type: user_ref }
    tasks:
      - id: collect_docs
        title: Collect ID & bank documents
        agent: db_agent
        action: request_documents
        sla: { due: "start_date - 7d" }
      - id: manager_approval
        type: approval
        agent: approvals_agent
        action: approval
        approver: { rule: variable, ref: manager_id }
        depends_on: [collect_docs]
      ...

This module checks shape only: types, identifiers, unique task and variable ids, one SLA form per
task, the SLA grammar and bounded `inputs`. Everything FR-T3 asks beyond shape (acyclic graph, agents
in the registry, tools in the agent's allowlist, declared variables, approvers, no sensitive fields
in LLM-visible inputs, `pii_access` within the field → sink policy) is checked by
`onb_agents.templates.validate_template`, which needs the registry and the sink policy.

`version` and `status` belong to the template lifecycle (TPL-2): a draft may leave them out. A
template carries no `org_id`: the org comes from the signed token, and storage adds it.
Validation errors never echo the submitted value (`hide_input_in_errors`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Final, Literal, Self

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

__all__ = [
    "APPROVER_ROLES",
    "MAX_INPUTS_SIZE",
    "MAX_INPUT_DEPTH",
    "MAX_INPUT_STRING",
    "MAX_LIST",
    "MAX_TASKS",
    "RESERVED_VARIABLE_NAMES",
    "SINK_PATTERN",
    "Approver",
    "Identifier",
    "InputKey",
    "PiiAccess",
    "Sla",
    "Template",
    "TemplateTask",
    "Title",
    "Variable",
    "VariableType",
    "bounded_json",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

MAX_TASKS: Final = 100
MAX_LIST: Final = 50
MAX_INPUT_DEPTH: Final = 6
MAX_INPUT_STRING: Final = 2000
MAX_INPUTS_SIZE: Final = 16_000
_INT64_MIN: Final = -(2**63)
_INT64_MAX: Final = 2**63 - 1
APPROVER_ROLES: Final = frozenset({"HR", "HR_ADMIN", "MANAGER"})
# Words of the `when` language: a variable with one of these names could never be referenced.
RESERVED_VARIABLE_NAMES: Final = frozenset({"and", "or", "not", "in", "true", "false", "null"})
SINK_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$"
_SLA_DUE: Final = re.compile(r"\s*([a-z][a-z0-9_]{0,63})\s*(?:([+-])\s*(\d{1,4})\s*([dh]))?\s*")

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TemplateKey = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
ToolRef = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,63}(\.[a-z][a-z0-9_]{0,63})?$")]
Title = Annotated[StrictStr, Field(min_length=1, max_length=200)]
EnumValue = Annotated[StrictStr, Field(min_length=1, max_length=64)]
InputKey = Annotated[StrictStr, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
VariableType = Literal["string", "enum", "date", "user_ref", "number", "boolean"]


def _unique(values: tuple[str, ...], what: str) -> tuple[str, ...]:
    """Refuse repeats, naming the position only: a value may be anything the author typed."""
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value in seen:
            raise ValueError(f"{what}: item {index} repeats an earlier item")
        seen.add(value)
    return values


class Variable(BaseModel):
    """A hire variable the template reads (`role`, `start_date`, `manager_id`...)."""

    model_config = _FROZEN

    name: Identifier
    type: VariableType
    values: Annotated[tuple[EnumValue, ...], Field(max_length=MAX_LIST)] | None = None

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in RESERVED_VARIABLE_NAMES:
            raise ValueError(f"{value} is a word of the when language, not a variable name")
        return value

    @model_validator(mode="after")
    def _values_only_for_enums(self) -> Self:
        if self.type == "enum":
            if not self.values:
                raise ValueError(f"enum variable {self.name} needs values")
            _unique(self.values, f"variable {self.name} values")
        elif self.values is not None:
            raise ValueError(f"only an enum variable has values ({self.name} is {self.type})")
        return self


class Approver(BaseModel):
    """Who decides an approval task: a `user_ref` variable, a platform role, or one user id."""

    model_config = _FROZEN

    rule: Literal["variable", "role", "user"]
    ref: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_:-]{1,128}$")]

    @model_validator(mode="after")
    def _known_role(self) -> Self:
        if self.rule == "role" and self.ref not in APPROVER_ROLES:
            raise ValueError(f"a role approver is one of {', '.join(sorted(APPROVER_ROLES))}")
        return self


class PiiAccess(BaseModel):
    """The vault fields a task may resolve, and the one sink that resolves them (PRD §7.10)."""

    model_config = _FROZEN

    fields: tuple[Identifier, ...] = Field(min_length=1, max_length=MAX_LIST)
    sink: Annotated[StrictStr, Field(pattern=SINK_PATTERN)]

    @field_validator("fields")
    @classmethod
    def _unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "pii_access fields")


class Sla(BaseModel):
    """`due` is a date variable with an optional offset: `start_date - 7d`, `start_date + 48h`."""

    model_config = _FROZEN

    due: Annotated[StrictStr, Field(min_length=1, max_length=64)]

    @field_validator("due")
    @classmethod
    def _grammar(cls, value: str) -> str:
        if _SLA_DUE.fullmatch(value) is None:
            raise ValueError("sla.due is a date variable with an optional offset, like start_date - 7d")
        return value

    def parts(self) -> tuple[str, int, Literal["d", "h"]]:
        """`(variable, signed offset, unit)`; no offset reads as `0d`."""
        match = _SLA_DUE.fullmatch(self.due)
        if match is None:  # pragma: no cover - the field validator refuses such a value
            raise ValueError("sla.due does not parse")
        variable, sign, amount, unit = match.groups()
        offset = int(amount or 0) * (-1 if sign == "-" else 1)
        return variable, offset, "h" if unit == "h" else "d"


def _size(value: object) -> int:
    """Characters of text in `value` (keys, strings and numbers as written). Refused: a string over the
    per-string cap, an integer outside int64 (it could not be stored) and a list over `MAX_LIST`."""
    if isinstance(value, str):
        if len(value) > MAX_INPUT_STRING:
            raise ValueError(f"an inputs string is at most {MAX_INPUT_STRING} characters")
        return len(value)
    if isinstance(value, Mapping):
        return sum(len(str(key)) + _size(item) for key, item in value.items())
    if isinstance(value, list):
        if len(value) > MAX_LIST:
            raise ValueError(f"an inputs list holds at most {MAX_LIST} items")
        return sum(_size(item) for item in value)
    if isinstance(value, int) and not isinstance(value, bool) and not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError("an inputs integer must fit in 64 bits")
    return len(repr(value)) if isinstance(value, int | float) and not isinstance(value, bool) else 1


def _depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0


def bounded_json[J: dict[str, JsonValue]](value: J, *, depth: int, size: int, what: str = "inputs") -> J:
    """`value` unchanged, or `ValueError` when it nests deeper than `depth` or holds more than `size`
    characters (or one string over `MAX_INPUT_STRING`). Used for task `inputs` and agent answers."""
    if _depth(value) > depth:
        raise ValueError(f"{what} nest at most {depth} levels deep")
    if _size(value) > size:
        raise ValueError(f"{what} hold at most {size} characters in total")
    return value


class TemplateTask(BaseModel):
    """One node of the template graph. `inputs` reach the task's agent, so they are LLM-visible."""

    model_config = _FROZEN

    id: Identifier
    title: Title
    agent: Identifier
    action: Identifier
    type: Literal["action", "approval"] = "action"
    depends_on: tuple[Identifier, ...] = Field(default=(), max_length=MAX_LIST)
    when: Annotated[StrictStr, Field(min_length=1, max_length=512)] | None = None
    inputs: dict[InputKey, JsonValue] = Field(default_factory=dict)
    tools: tuple[ToolRef, ...] = Field(default=(), max_length=MAX_LIST)
    pii_access: PiiAccess | None = None
    approver: Approver | None = None
    requires_approval: StrictBool = False
    sla: Sla | None = None
    sla_hours: Annotated[StrictInt, Field(gt=0, le=24 * 365)] | None = None

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "depends_on")

    @field_validator("tools")
    @classmethod
    def _unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "tools")

    @field_validator("inputs")
    @classmethod
    def _bounded_inputs(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return bounded_json(value, depth=MAX_INPUT_DEPTH, size=MAX_INPUTS_SIZE)

    @model_validator(mode="after")
    def _one_sla_form(self) -> Self:
        if self.sla is not None and self.sla_hours is not None:
            raise ValueError(f"task {self.id} sets both sla and sla_hours; use one")
        return self


class Template(BaseModel):
    """A template draft or version: variables and a task graph (`depends_on` edges)."""

    model_config = _FROZEN

    key: TemplateKey
    name: Title | None = None
    version: Annotated[StrictInt, Field(ge=1)] | None = None
    status: Literal["draft", "published", "archived"] = "draft"
    variables: tuple[Variable, ...] = Field(default=(), max_length=MAX_LIST)
    tasks: tuple[TemplateTask, ...] = Field(min_length=1, max_length=MAX_TASKS)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        _unique(tuple(variable.name for variable in self.variables), "variables")
        _unique(tuple(task.id for task in self.tasks), "task ids")
        return self

    def task(self, task_id: str) -> TemplateTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def variable(self, name: str) -> Variable | None:
        return next((variable for variable in self.variables if variable.name == name), None)
