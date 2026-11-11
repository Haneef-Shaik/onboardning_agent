"""Comparisons the `when` evaluator would decide wrongly, refused before it runs.

The evaluator reads `role == 1` as false (and `role != 1` as true) because the kinds differ, and
compares dates as text, so `start_date >= '2026-1-5'` gives the wrong answer. Publish-time validation
(TPL-1) refuses most of these, but a published `Template` can be built without it, so the compiler
checks again: every compared pair must be of one kind (text, number or bool), and a literal compared
with a date variable must be a real `YYYY-MM-DD` date. `null` may be compared with anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from onb_contracts.templates import Template

from onb_agents.templates import WhenExpression

from .errors import CompileError, CompileReason
from .variables import ISO_DATE, HireValue

__all__ = ["check_condition"]


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return "text"


def _iso_date(value: str) -> bool:
    if not ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_condition(
    condition: WhenExpression, template: Template, values: Mapping[str, HireValue], task_id: str
) -> None:
    """Raise `CompileError(BAD_CONDITION)` for a comparison across kinds or with a non-ISO date."""
    for left, right in condition.name_pairs():
        if _kind(values[left]) != _kind(values[right]):
            raise CompileError(
                CompileReason.BAD_CONDITION, f"when compares {left} with {right} of another kind", task_id
            )
    for name, literal in condition.comparisons():
        if literal is None:
            continue
        if _kind(values[name]) != _kind(literal):
            raise CompileError(
                CompileReason.BAD_CONDITION, f"when compares {name} with a value of another kind", task_id
            )
        variable = template.variable(name)
        if variable is not None and variable.type == "date" and not _iso_date(str(literal)):
            raise CompileError(CompileReason.BAD_CONDITION, f"when compares date {name} with a non-ISO date", task_id)
