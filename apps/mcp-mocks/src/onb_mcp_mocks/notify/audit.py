"""A refused grant leaves an audit record and a guardrail event (PRD §7.10: "a guardrail event and audit
record are written"), both in the caller's org:

* the grant record (PII-4's `GrantAudit` hook): `actor=agent:<agent>`, `action=pii.grant.resolve`,
  `target=pii_grant:<jti>` (or `pii_grant:unverified` when the signature did not verify),
  `outcome=denied`, `reason=<RejectReason>`;
* the guardrail decision, through GR-1's `DecisionRecorder`: `action=guardrail.sink.grant`,
  `target=task:<run_id>/<task_id>`, `outcome=denied`, and a `guardrail.outcome` span event.

Reasons are codes only. The rejection's free-text detail names the jti and tokens: it stays in the
service log, never in a record the agent or the UI reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from onb_crypto import GrantAuditRecord, RejectReason
from onb_data import Actor, Auditor, AuditOutcome
from onb_guardrails import DecisionRecorder, GuardContext
from onb_guardrails.decision import GuardrailCheck, GuardrailDecision, GuardrailOutcome

__all__ = ["GRANT_ACTION", "UNVERIFIED", "Caller", "CallerGrantAudit", "record_guardrail"]

GRANT_ACTION = "pii.grant.resolve"
UNVERIFIED = "unverified"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


@dataclass(frozen=True, slots=True)
class Caller:
    """Who presented the grant, from the hub-signed call context (never from the grant)."""

    org_id: str
    agent: str
    run_id: str


def _safe(value: str | None) -> str:
    return value if value is not None and _ID.fullmatch(value) else UNVERIFIED


class CallerGrantAudit:
    """PII-4's `GrantAudit` for one call: records the rejection against the agent that presented it."""

    def __init__(self, auditor: Auditor, caller: Caller) -> None:
        self._auditor = auditor
        self._caller = caller
        self.rejection: GrantAuditRecord | None = None

    async def record(self, record: GrantAuditRecord) -> None:
        self.rejection = record
        await self._auditor.audit(
            actor=Actor.agent(self._caller.agent),
            action=GRANT_ACTION,
            target=f"pii_grant:{_safe(record.jti)}",
            outcome=AuditOutcome.DENIED,
            reason=record.reason.value,
        )


async def record_guardrail(auditor: Auditor, caller: Caller, task_id: str | None, reason: RejectReason) -> None:
    """The guardrail decision for a refused grant: an audit record plus a `guardrail.outcome` span event."""
    context = GuardContext(agent=caller.agent, run_id=_safe(caller.run_id), task_id=_safe(task_id))
    decision = GuardrailDecision(GuardrailCheck.SINK_GRANT, GuardrailOutcome.BLOCKED, reason.value)
    await DecisionRecorder(auditor, context).record(decision)
