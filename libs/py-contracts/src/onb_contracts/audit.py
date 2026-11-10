"""Audit log contracts (PRD §7.6 Audit, §10 `GET /audit`, HR_ADMIN only).

A record says who acted (a user or an agent), what they did (`action`, a dotted name), to which
`target` (ids and `ptk_` tokens, never raw PII), with what `outcome` and why. `trace_id` links it to
the trace (32 hex digits, or null when the action ran outside a span). Records are append-only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from onb_contracts.envelope import Envelope

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# The filter bounds `GET /audit` accepts.
AUDIT_ACTION_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
AUDIT_ACTOR_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
AUDIT_TARGET_PATTERN = r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:/-]+$"
MAX_AUDIT_ACTION_LENGTH = 64
MAX_AUDIT_TARGET_LENGTH = 256
MAX_AUDIT_LIMIT = 200
DEFAULT_AUDIT_LIMIT = 50
# Paging reaches at most this many records deep ((page - 1) * limit).
MAX_AUDIT_SKIP = 10_000


class AuditActorType(StrEnum):
    USER = "user"
    AGENT = "agent"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"


class AuditActor(BaseModel):
    model_config = _FROZEN

    type: AuditActorType
    id: str


class AuditRecord(BaseModel):
    model_config = _FROZEN

    id: str
    org_id: str
    actor: AuditActor
    action: str
    target: str
    outcome: AuditOutcome
    reason: str | None
    trace_id: str | None
    at: datetime


class AuditRecordsEnvelope(Envelope[list[AuditRecord]]):
    """One page of the org's audit records, newest first; `meta.page` has the total."""
