"""Every guardrail decision becomes an audit record and a `guardrail.outcome` span event (PRD §8.1).

The audit record is written first, through the `onb_data.Auditor` protocol (append-only
`AuditLog` in services, `InMemoryAuditLog` in tests); a failed write propagates, so the guarded call
fails closed (AUD-1 contract). The span event follows; the record carries the span's `trace_id`
(AUD-1), which is how a trace links to its audit trail:

    guardrail.check     input.pii | input.injection | output.pii | output.schema | budget | external.*
    guardrail.outcome   pass | redacted | repaired | blocked | failed
    guardrail.reason    codes and counts only (absent when there is none)

The record id is not put on the event: the OBS-1 span scrubber can mistake a digit run inside a hex
id for a mobile number and redact it.

The audit record is `actor=agent:<agent>`, `action=guardrail.<check>`, `target=task:<run_id>/<task_id>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from onb_data import Actor, Auditor, AuditOutcome
from opentelemetry import trace

from onb_guardrails.decision import GuardrailDecision, GuardrailOutcome

EVENT_NAME: Final = "guardrail.outcome"
_ID: Final = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_AUDIT_OUTCOME: Final = {
    GuardrailOutcome.PASSED: AuditOutcome.SUCCESS,
    GuardrailOutcome.REDACTED: AuditOutcome.SUCCESS,
    GuardrailOutcome.REPAIRED: AuditOutcome.SUCCESS,
    GuardrailOutcome.BLOCKED: AuditOutcome.DENIED,
    GuardrailOutcome.FAILED: AuditOutcome.FAILED,
}


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Who is calling the model, for which run and task. Ids only: letters, digits and _ . : -."""

    agent: str
    run_id: str
    task_id: str

    def __post_init__(self) -> None:
        for name in ("agent", "run_id", "task_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError(f"{name} must be 1-64 characters of letters, digits and _ . : -")

    @property
    def target(self) -> str:
        return f"task:{self.run_id}/{self.task_id}"

    def span_attributes(self) -> dict[str, str]:
        return {"agent.name": self.agent, "run_id": self.run_id, "task_id": self.task_id}


class DecisionRecorder:
    def __init__(self, auditor: Auditor, context: GuardContext) -> None:
        self._auditor = auditor
        self._context = context

    async def record(self, decision: GuardrailDecision) -> str:
        """Audit `decision`, then add it to the current span as a `guardrail.outcome` event."""
        audit_id = await self._auditor.audit(
            actor=Actor.agent(self._context.agent),
            action=f"guardrail.{decision.check}",
            target=self._context.target,
            outcome=_AUDIT_OUTCOME[decision.outcome],
            reason=decision.reason,
        )
        attributes = {"guardrail.check": str(decision.check), "guardrail.outcome": str(decision.outcome)}
        if decision.reason is not None:
            attributes["guardrail.reason"] = decision.reason
        trace.get_current_span().add_event(EVENT_NAME, attributes)
        return audit_id
