"""Test doubles for code that writes or reads the audit log. Test-only: production code never imports it.

`InMemoryAuditLog` keeps `AuditLog`'s contract: it builds records with the same `build_record(...)`
(so it refuses the same input), stamps the current span's trace id, keeps records per org (read in
the current tenant scope only), lists newest first with the same page bounds, and has no update or
delete. `now`/`trace` override the clock and trace id for tests that need fixed values.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .audit import (
    Actor,
    AuditEntry,
    AuditOutcome,
    AuditPage,
    AuditQuery,
    build_record,
    check_page,
    current_trace_id,
)
from .context import current_tenant

__all__ = ["InMemoryAuditLog"]


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._entries: tuple[AuditEntry, ...] = ()
        self.now: datetime | None = None
        self.trace: str | None = None
        self.indexes_ensured = 0
        self.list_calls = 0
        self.last_list: tuple[AuditQuery, int, int] | None = None

    async def ensure_indexes(self) -> None:
        self.indexes_ensured += 1

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        document = build_record(
            actor=actor,
            action=action,
            target=target,
            outcome=outcome,
            reason=reason,
            trace_id=self.trace if self.trace is not None else current_trace_id(),
            at=self.now or datetime.now(UTC),
        )
        entry = AuditEntry.from_document({**document, "org_id": current_tenant().org_id})
        self._entries = (*self._entries, entry)
        return entry.id

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> AuditPage:
        skip = check_page(page, limit)
        self.list_calls += 1
        self.last_list = (query, page, limit)
        org_id = current_tenant().org_id
        matching = sorted(
            (e for e in self._entries if e.org_id == org_id and _matches(e, query)),
            key=lambda e: (e.at, e.id),
            reverse=True,
        )
        return AuditPage(entries=tuple(matching[skip : skip + limit]), total=len(matching), page=page, limit=limit)


def _matches(entry: AuditEntry, query: AuditQuery) -> bool:
    exact = (
        (query.actor_type, entry.actor.type),
        (query.actor_id, entry.actor.id),
        (query.action, entry.action),
        (query.outcome, entry.outcome),
        (query.target, entry.target),
    )
    if any(wanted is not None and wanted != actual for wanted, actual in exact):
        return False
    if query.since is not None and entry.at < query.since:
        return False
    return query.until is None or entry.at <= query.until
