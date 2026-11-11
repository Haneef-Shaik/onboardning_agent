"""SLA due dates resolved at compile time (PRD §6.1 `sla: { due: "start_date - 7d" }`).

    start_date 2026-12-01, `start_date - 7d`  ->  "2026-11-24"
    start_date 2026-12-01, `start_date + 36h` ->  "2026-12-02T12:00:00+00:00"

A day offset gives a date. An hour offset counts from 00:00 UTC of the date, because a hire's date
variable carries no time zone (ADR 0008). `sla_hours` (the fixture form) is relative to when the task
starts, so it is left on the task for the runtime and never resolved here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta

from onb_contracts.templates import Sla, Template

from .errors import CompileError, CompileReason
from .variables import HireValue, as_date

__all__ = ["resolve_due"]


def resolve_due(sla: Sla, template: Template, values: Mapping[str, HireValue], task_id: str) -> str:
    """The ISO date (day offsets) or UTC datetime (hour offsets) the task is due by."""
    name, offset, unit = sla.parts()
    variable = template.variable(name)
    if variable is None or variable.type != "date":
        raise CompileError(CompileReason.BAD_SLA, f"sla.due reads {name}, which is not a declared date", task_id)
    if name not in values:
        raise CompileError(CompileReason.MISSING_VARIABLE, f"sla.due reads {name}, which the hire lacks", task_id)
    day = as_date(values[name])
    try:
        if unit == "d":
            return (day + timedelta(days=offset)).isoformat()
        return (datetime.combine(day, time(), tzinfo=UTC) + timedelta(hours=offset)).isoformat()
    except OverflowError:
        raise CompileError(CompileReason.BAD_SLA, "sla.due falls outside the calendar", task_id) from None
