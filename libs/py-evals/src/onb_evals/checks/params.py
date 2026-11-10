"""Typed parameters for every check in the `evals/README.md` catalog.

A case's check is `{"type": <name>, ...params}`. Each model forbids unknown keys, so a misspelt
parameter fails when the dataset loads, not silently at grading time.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from onb_evals.paths import split_path

__all__ = ["PARAMS", "CheckParams", "Scope"]

NonEmptyStr = Annotated[StrictStr, Field(min_length=1)]
Scope = Literal["output", "tool_args", "llm_input", "trace"]
ContractTarget = Literal["template", "change_request", "approval_packet", "message"]


class CheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    when_present: StrictBool = False


class _PathParams(CheckParams):
    path: NonEmptyStr

    @field_validator("path")
    @classmethod
    def _path_is_well_formed(cls, value: str) -> str:
        split_path(value)
        return value


class StatusIn(CheckParams):
    values: list[NonEmptyStr] = Field(min_length=1)


class SchemaValid(CheckParams):
    target: ContractTarget


class DagValid(CheckParams):
    pass


class TaskCoverage(CheckParams):
    expected_actions: list[NonEmptyStr] = Field(min_length=1)
    min_recall: float = Field(ge=0.0, le=1.0)


class ForbiddenActions(CheckParams):
    actions: list[NonEmptyStr] = Field(min_length=1)


class Dependency(CheckParams):
    from_action: NonEmptyStr
    to_action: NonEmptyStr
    on: Literal["output", "base_plus_diff"] = "output"


class TaskCondition(CheckParams):
    action: NonEmptyStr
    must_reference: list[NonEmptyStr] = Field(min_length=1)


class VariableDeclared(CheckParams):
    name: NonEmptyStr


class PiiAccessForbidden(CheckParams):
    fields: list[NonEmptyStr] = Field(min_length=1)


class DiffAddsAction(CheckParams):
    action: NonEmptyStr


class DiffRemovesNone(CheckParams):
    pass


class FieldEquals(_PathParams):
    value: Any


class FieldIn(_PathParams):
    values: list[Any] = Field(min_length=1)


class FieldContains(_PathParams):
    value: Any
    case_insensitive: StrictBool = False


class FieldEmpty(_PathParams):
    pass


class ContainsValues(_PathParams):
    values: list[NonEmptyStr] = Field(min_length=1)


class _Pattern(CheckParams):
    pattern: NonEmptyStr

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"pattern does not compile: {error}") from None
        return value


class RegexAbsent(_Pattern):
    path: NonEmptyStr | None = None

    @field_validator("path")
    @classmethod
    def _path_is_well_formed(cls, value: str | None) -> str | None:
        if value is not None:
            split_path(value)
        return value


class MaxLength(_PathParams):
    value: StrictInt = Field(ge=0)


class _ToolMatch(CheckParams):
    tool: NonEmptyStr | None = None
    any_of: list[NonEmptyStr] | None = Field(default=None, min_length=1)
    args_subset: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_tool_selector(self) -> Self:
        if (self.tool is None) == (self.any_of is None):
            raise ValueError("give exactly one of tool or any_of")
        return self

    def tool_names(self) -> frozenset[str]:
        return frozenset([self.tool] if self.tool is not None else self.any_of or [])


class ToolCalled(_ToolMatch):
    pass


class ToolNotCalled(_ToolMatch):
    pass


class MaxSteps(CheckParams):
    value: StrictInt = Field(ge=0)


class FinalState(CheckParams):
    collection: NonEmptyStr
    filter: dict[str, Any]
    expect: dict[str, Any] | None = None
    expect_count: StrictInt | None = Field(default=None, ge=0)
    expect_count_gte: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_expectation(self) -> Self:
        given = [self.expect is not None, self.expect_count is not None, self.expect_count_gte is not None]
        if sum(given) != 1:
            raise ValueError("give exactly one of expect, expect_count or expect_count_gte")
        return self


class NoRawPii(CheckParams):
    scope: list[Scope] = Field(min_length=1)


class PiiTokenBinding(_PathParams):
    subject_id: NonEmptyStr
    field: NonEmptyStr


class PiiTokensAllowedFields(CheckParams):
    fields: list[NonEmptyStr] = Field(min_length=1)
    subject_id: NonEmptyStr
    scope: list[Scope] = Field(min_length=1)


class CitationIncludes(CheckParams):
    run_id: NonEmptyStr
    task_id: NonEmptyStr


class AuditNoCrossTenant(CheckParams):
    pass


class AuditNoAllowedCallMatching(_Pattern):
    pass


PARAMS: dict[str, type[CheckParams]] = {
    "status_in": StatusIn,
    "schema_valid": SchemaValid,
    "dag_valid": DagValid,
    "task_coverage": TaskCoverage,
    "forbidden_actions": ForbiddenActions,
    "dependency": Dependency,
    "task_condition": TaskCondition,
    "variable_declared": VariableDeclared,
    "pii_access_forbidden": PiiAccessForbidden,
    "diff_adds_action": DiffAddsAction,
    "diff_removes_none": DiffRemovesNone,
    "field_equals": FieldEquals,
    "field_in": FieldIn,
    "field_contains": FieldContains,
    "field_empty": FieldEmpty,
    "contains_all": ContainsValues,
    "contains_any": ContainsValues,
    "regex_absent": RegexAbsent,
    "max_length": MaxLength,
    "tool_called": ToolCalled,
    "tool_not_called": ToolNotCalled,
    "max_steps": MaxSteps,
    "final_state": FinalState,
    "no_raw_pii": NoRawPii,
    "pii_token_binding": PiiTokenBinding,
    "pii_tokens_allowed_fields": PiiTokensAllowedFields,
    "citation_includes": CitationIncludes,
    "audit_no_cross_tenant": AuditNoCrossTenant,
    "audit_no_allowed_call_matching": AuditNoAllowedCallMatching,
}
