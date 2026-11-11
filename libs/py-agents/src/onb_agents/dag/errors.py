"""Why a compile is refused. A compile never guesses: anything it cannot decide fails it, by name."""

from __future__ import annotations

from enum import StrEnum

from onb_guardrails.pii import redact

__all__ = ["CompileError", "CompileReason"]


class CompileReason(StrEnum):
    NOT_PUBLISHED = "not_published"
    MISSING_VARIABLE = "missing_variable"
    BAD_VARIABLE = "bad_variable"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    CYCLE = "cycle"
    BAD_CONDITION = "bad_condition"
    BAD_SLA = "bad_sla"
    BAD_INPUTS = "bad_inputs"


class CompileError(ValueError):
    """The template version and these variables do not compile. Messages name variables and tasks,
    never a variable's value."""

    def __init__(self, reason: CompileReason, message: str, task_id: str | None = None) -> None:
        where = f"task {task_id}: " if task_id else ""
        super().__init__(f"{reason.value}: {where}{redact(message)}")
        self.reason = reason
        self.task_id = task_id
