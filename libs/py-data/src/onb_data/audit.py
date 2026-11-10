"""The audit log: who did what to which target, with what outcome and why (PRD §7.6, §7.9 Telemetry).

One call records a privileged action, inside the caller's tenant scope:

    audit_log = AuditLog(store)
    with tenant_scope(org_id_from_token):
        await audit_log.audit(
            actor=Actor.user(principal.user_id),      # or Actor.agent("db_agent")
            action="pii.reveal",                      # dotted, lower case
            target="hire:h_123/pan",                  # ids and ptk_ tokens only, never raw PII
            outcome=AuditOutcome.SUCCESS,             # or DENIED / FAILED
            reason="verifying documents",             # optional, free text, no raw PII
        )

The record is `{_id, org_id, actor: {type, id}, action, target, outcome, reason, trace_id, at}`.
`org_id` comes from the tenant context, `trace_id` from the current OpenTelemetry span (32 hex
digits, or null outside a span) and `at` from the clock (UTC). The log is append-only: it is backed by
`store.append_only("audit_log")`, which has no update or delete path. HR_ADMIN reads it through
`list(...)` (filters, newest first, paged).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from .store import DataStore

__all__ = [
    "AUDIT_COLLECTION",
    "MAX_AUDIT_PAGE_LIMIT",
    "Actor",
    "ActorType",
    "AuditEntry",
    "AuditLog",
    "AuditOutcome",
    "AuditPage",
    "AuditQuery",
    "current_trace_id",
]

AUDIT_COLLECTION = "audit_log"
MAX_AUDIT_PAGE_LIMIT = 200
MAX_TARGET_LENGTH = 256
MAX_REASON_LENGTH = 1000

_ACTION = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_MAX_ACTION_LENGTH = 64
_ACTOR_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"


def _actor_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("an actor id must be a string")
    if not _ACTOR_ID.fullmatch(value):
        raise ValueError("an actor id is 1-128 characters of letters, digits and _ . : @ -")
    return value


def _action(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("an action must be a string")
    if len(value) > _MAX_ACTION_LENGTH or not _ACTION.fullmatch(value):
        raise ValueError("an action is a dotted lower-case name such as 'pii.reveal' (at most 64 characters)")
    return value


def _text(value: object, *, what: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a string")
    if not value or len(value) > limit:
        raise ValueError(f"{what} must be 1-{limit} characters")
    return value


def _enum[E: StrEnum](value: object, kind: type[E]) -> E:
    if not isinstance(value, kind):
        raise TypeError(f"expected a {kind.__name__}")
    return value


def _aware(value: object, *, what: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{what} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{what} must carry a time zone")
    return value


@dataclass(frozen=True, slots=True)
class Actor:
    """Who acted: a user (their user id) or an agent (its registry name)."""

    type: ActorType
    id: str

    def __post_init__(self) -> None:
        _enum(self.type, ActorType)
        _actor_id(self.id)

    @classmethod
    def user(cls, user_id: str) -> Actor:
        return cls(ActorType.USER, user_id)

    @classmethod
    def agent(cls, name: str) -> Actor:
        return cls(ActorType.AGENT, name)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One stored record, as read back."""

    id: str
    org_id: str
    actor: Actor
    action: str
    target: str
    outcome: AuditOutcome
    reason: str | None
    trace_id: str | None
    at: datetime

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> AuditEntry:
        actor = document["actor"]
        return cls(
            id=str(document["_id"]),
            org_id=document["org_id"],
            actor=Actor(ActorType(actor["type"]), actor["id"]),
            action=document["action"],
            target=document["target"],
            outcome=AuditOutcome(document["outcome"]),
            reason=document.get("reason"),
            trace_id=document.get("trace_id"),
            at=document["at"],
        )


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """Filters for `AuditLog.list`. Every field is optional; values are matched exactly."""

    actor_type: ActorType | None = None
    actor_id: str | None = None
    action: str | None = None
    outcome: AuditOutcome | None = None
    target: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.actor_type is not None:
            _enum(self.actor_type, ActorType)
        if self.outcome is not None:
            _enum(self.outcome, AuditOutcome)
        if self.actor_id is not None:
            _actor_id(self.actor_id)
        if self.action is not None:
            _action(self.action)
        if self.target is not None:
            _text(self.target, what="target", limit=MAX_TARGET_LENGTH)
        since = None if self.since is None else _aware(self.since, what="since")
        until = None if self.until is None else _aware(self.until, what="until")
        if since is not None and until is not None and since > until:
            raise ValueError("since must not be after until")

    def to_filter(self) -> dict[str, Any]:
        exact = {
            "actor.type": None if self.actor_type is None else self.actor_type.value,
            "actor.id": self.actor_id,
            "action": self.action,
            "outcome": None if self.outcome is None else self.outcome.value,
            "target": self.target,
        }
        query: dict[str, Any] = {key: value for key, value in exact.items() if value is not None}
        window = {op: value for op, value in (("$gte", self.since), ("$lte", self.until)) if value is not None}
        if window:
            query["at"] = window
        return query


@dataclass(frozen=True, slots=True)
class AuditPage:
    entries: tuple[AuditEntry, ...]
    total: int
    page: int
    limit: int


def current_trace_id() -> str | None:
    """The current span's trace id as 32 hex digits; `None` outside a recorded span."""
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    """The org's audit log: `audit(...)` appends a record, `list(...)` reads them. Nothing else."""

    __slots__ = ("_clock", "_repo", "_store", "_trace_id")

    def __init__(
        self,
        store: DataStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        trace_id: Callable[[], str | None] = current_trace_id,
    ) -> None:
        self._store = store
        self._repo = store.append_only(AUDIT_COLLECTION)
        self._clock = clock
        self._trace_id = trace_id

    async def audit(
        self,
        *,
        actor: Actor,
        action: str,
        target: str,
        outcome: AuditOutcome,
        reason: str | None = None,
    ) -> str:
        """Append one record in the current tenant scope and return its id."""
        if not isinstance(actor, Actor):
            raise TypeError("actor must be an Actor (Actor.user(...) or Actor.agent(...))")
        record_id = uuid.uuid4().hex
        document = {
            "_id": record_id,
            "actor": {"type": actor.type.value, "id": actor.id},
            "action": _action(action),
            "target": _text(target, what="target", limit=MAX_TARGET_LENGTH),
            "outcome": _enum(outcome, AuditOutcome).value,
            "reason": None if reason is None else _text(reason, what="reason", limit=MAX_REASON_LENGTH),
            "trace_id": self._trace_id(),
            "at": self._clock(),
        }
        await self._repo.insert_one(document)
        return record_id

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> AuditPage:
        """One page of the current org's records matching `query`, newest first."""
        if not isinstance(query, AuditQuery):
            raise TypeError("query must be an AuditQuery")
        if page < 1:
            raise ValueError("page starts at 1")
        if not 1 <= limit <= MAX_AUDIT_PAGE_LIMIT:
            raise ValueError(f"limit must be 1-{MAX_AUDIT_PAGE_LIMIT}")
        mongo_filter = query.to_filter()
        documents = await self._repo.find(
            mongo_filter, sort=[("at", -1), ("_id", -1)], skip=(page - 1) * limit, limit=limit
        )
        total = await self._repo.count(mongo_filter)
        entries = tuple(AuditEntry.from_document(document) for document in documents)
        return AuditPage(entries=entries, total=total, page=page, limit=limit)

    async def ensure_indexes(self) -> None:
        """The index the HR_ADMIN view reads by: the org's records newest first."""
        await self._store.ensure_index(AUDIT_COLLECTION, [("org_id", 1), ("at", -1), ("_id", -1)], name="org_at")
