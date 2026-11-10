"""Guardrail layer (PRD §5.3, §8.1): every LLM input, output and budget checked, connector agnostic.

`GuardedChatModel` is the entry point; `onb_guardrails.pii` holds the PII validators and detectors.
"""

from onb_guardrails.budget import Budget, BudgetLimits, BudgetUsage
from onb_guardrails.decision import (
    BudgetExceededError,
    ExternalGuardBlockedError,
    GuardrailCheck,
    GuardrailDecision,
    GuardrailError,
    GuardrailOutcome,
    InjectionBlockedError,
    StructuredOutputError,
)
from onb_guardrails.delimit import UNTRUSTED_NOTICE, UNTRUSTED_TAG, delimit_untrusted
from onb_guardrails.external import BedrockGuardrails, ExternalGuard, ExternalVerdict
from onb_guardrails.guarded import GuardedChatModel
from onb_guardrails.injection import PATTERN_IDS, detect_injection
from onb_guardrails.messages import trusted_message
from onb_guardrails.recorder import EVENT_NAME, DecisionRecorder, GuardContext

__all__ = [
    "EVENT_NAME",
    "PATTERN_IDS",
    "PROJECT",
    "UNTRUSTED_NOTICE",
    "UNTRUSTED_TAG",
    "BedrockGuardrails",
    "Budget",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetUsage",
    "DecisionRecorder",
    "ExternalGuard",
    "ExternalGuardBlockedError",
    "ExternalVerdict",
    "GuardContext",
    "GuardedChatModel",
    "GuardrailCheck",
    "GuardrailDecision",
    "GuardrailError",
    "GuardrailOutcome",
    "InjectionBlockedError",
    "StructuredOutputError",
    "delimit_untrusted",
    "detect_injection",
    "trusted_message",
]

PROJECT = "py-guardrails"
