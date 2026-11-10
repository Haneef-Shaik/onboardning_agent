"""Guardrail decisions and the errors that stop a call (PRD §8.1).

A decision names the check, its outcome and a reason built only from codes and counts (pattern ids,
PII type counts, budget figures): never the checked text, so it is safe to audit and trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GuardrailCheck(StrEnum):
    INPUT_INJECTION = "input.injection"
    INPUT_PII = "input.pii"
    OUTPUT_PII = "output.pii"
    OUTPUT_SCHEMA = "output.schema"
    BUDGET = "budget"
    EXTERNAL_INPUT = "external.input"
    EXTERNAL_OUTPUT = "external.output"


class GuardrailOutcome(StrEnum):
    PASSED = "pass"
    REDACTED = "redacted"
    REPAIRED = "repaired"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    check: GuardrailCheck
    outcome: GuardrailOutcome
    reason: str | None = None


class GuardrailError(Exception):
    """A guardrail stopped the call. The message holds codes and counts only, never checked text."""


class InjectionBlockedError(GuardrailError):
    def __init__(self, patterns: tuple[str, ...]) -> None:
        super().__init__(f"prompt injection blocked: {', '.join(patterns)}")
        self.patterns = patterns


class BudgetExceededError(GuardrailError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"budget exceeded: {reason}")
        self.reason = reason


class StructuredOutputError(GuardrailError):
    """The model's structured output was still invalid after the one repair attempt."""

    def __init__(self, schema: str, reason: str) -> None:
        super().__init__(f"structured output for {schema} is invalid after one repair: {reason}")
        self.schema = schema
        self.reason = reason


class ExternalGuardBlockedError(GuardrailError):
    """An external guard (Bedrock Guardrails) intervened, or could not be reached (fail closed)."""

    def __init__(self, guard: str, reason: str) -> None:
        super().__init__(f"{guard} blocked the call: {reason}")
        self.guard = guard
        self.reason = reason
