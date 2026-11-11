"""A hire's variables, checked against the template's declarations before anything reads them.

Only declared variables are kept: a hire record carries more (name, notes, vault tokens) and none of
it reaches a condition or the DAG. Each kept value must match its declared type exactly; dates become
ISO `YYYY-MM-DD` strings, the form `when` conditions compare. A variable nothing reads may be absent.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Final

from onb_contracts.templates import Template, Variable

from .errors import CompileError, CompileReason

__all__ = ["HireValue", "as_date", "hire_values"]

HireValue = str | int | float | bool
_ISO_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_TEXT: Final = 512


def hire_values(template: Template, variables: object) -> dict[str, HireValue]:
    """The declared variables the hire provides, each checked against its type."""
    if not isinstance(variables, Mapping):
        raise CompileError(CompileReason.BAD_VARIABLE, "variables are a mapping of name to value")
    return {
        variable.name: _checked(variable, variables[variable.name])
        for variable in template.variables
        if variable.name in variables
    }


def _checked(variable: Variable, value: object) -> HireValue:
    checked = _CHECKS[variable.type](variable, value)
    if checked is None:
        raise CompileError(CompileReason.BAD_VARIABLE, f"variable {variable.name} is not a valid {variable.type}")
    return checked


def _text(_: Variable, value: object) -> HireValue | None:
    return value if isinstance(value, str) and 0 < len(value) <= _MAX_TEXT else None


def _enum(variable: Variable, value: object) -> HireValue | None:
    return value if isinstance(value, str) and variable.values is not None and value in variable.values else None


def _date(_: Variable, value: object) -> HireValue | None:
    if type(value) is date:  # a datetime is a date subclass, and not a date here
        return value.isoformat()
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _number(_: Variable, value: object) -> HireValue | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def _boolean(_: Variable, value: object) -> HireValue | None:
    return value if isinstance(value, bool) else None


_CHECKS = {
    "string": _text,
    "user_ref": _text,
    "enum": _enum,
    "date": _date,
    "number": _number,
    "boolean": _boolean,
}


def as_date(value: HireValue) -> date:
    """A checked date variable back as a `date` (it was stored as its ISO string)."""
    if not isinstance(value, str):  # pragma: no cover - hire_values holds every date as a str
        raise TypeError("a date variable is held as its ISO string")
    return date.fromisoformat(value)
