"""The case loader (evals/README.md "Case format"): one JSON object per line, validated up front."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from onb_evals.checks.params import PARAMS, CheckParams

__all__ = ["Actor", "CaseFormatError", "CheckSpec", "EvalCase", "SetupOp", "load_cases", "parse_case"]

Category = Literal["capability", "safety", "regression"]
_ID = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class CaseFormatError(ValueError):
    """A dataset line is not a valid case."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Actor(_Frozen):
    org_id: _ID
    role: Annotated[StrictStr, Field(min_length=1)]
    user_id: _ID | None = None
    run_id: _ID | None = None
    task_id: _ID | None = None


class SetupOp(_Frozen):
    op: Literal["update"]
    collection: _ID
    filter: dict[str, Any]
    set: dict[str, Any] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CheckSpec:
    type: str
    params: CheckParams


class _RawCase(_Frozen):
    id: _ID
    agent: _ID
    mode: _ID | None = None
    category: Category
    tags: tuple[StrictStr, ...] = ()
    actor: Actor
    input: dict[str, Any]
    setup: tuple[SetupOp, ...] = ()
    notes: StrictStr | None = None
    checks: list[dict[str, Any]] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    agent: str
    mode: str | None
    category: str
    tags: tuple[str, ...]
    actor: Actor
    input: Mapping[str, Any]
    setup: tuple[SetupOp, ...]
    checks: tuple[CheckSpec, ...]
    raw: Mapping[str, Any]


def _check_spec(raw: Mapping[str, Any], index: int) -> CheckSpec:
    kind = raw.get("type")
    params_model = PARAMS.get(kind) if isinstance(kind, str) else None
    if params_model is None:
        raise CaseFormatError(f"checks[{index}]: unknown check type {kind!r}")
    try:
        params = params_model.model_validate({k: v for k, v in raw.items() if k != "type"})
    except ValidationError as error:
        raise CaseFormatError(f"checks[{index}] ({kind}): {error}") from None
    return CheckSpec(type=str(kind), params=params)


def parse_case(raw: Mapping[str, Any]) -> EvalCase:
    """One validated case; `CaseFormatError` names what is wrong."""
    try:
        case = _RawCase.model_validate(raw)
    except ValidationError as error:
        raise CaseFormatError(f"case {raw.get('id')!r}: {error}") from None
    checks = tuple(_check_spec(check, index) for index, check in enumerate(case.checks))
    return EvalCase(
        id=case.id,
        agent=case.agent,
        mode=case.mode,
        category=case.category,
        tags=case.tags,
        actor=case.actor,
        input=case.input,
        setup=case.setup,
        checks=checks,
        raw=dict(raw),
    )


def _parse_line(line: str, number: int) -> EvalCase:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise CaseFormatError(f"line {number}: not JSON ({error.msg})") from None
    if not isinstance(raw, dict):
        raise CaseFormatError(f"line {number}: a case must be a JSON object")
    try:
        return parse_case(raw)
    except CaseFormatError as error:
        raise CaseFormatError(f"line {number}: {error}") from None


def load_cases(path: Path, *, agent: str | None = None) -> tuple[EvalCase, ...]:
    """Every case in a JSONL dataset (blank lines skipped), optionally only one agent's."""
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        case = _parse_line(line, number)
        if case.id in seen:
            raise CaseFormatError(f"line {number}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise CaseFormatError(f"{path}: no cases")
    return tuple(case for case in cases if agent is None or case.agent == agent)
