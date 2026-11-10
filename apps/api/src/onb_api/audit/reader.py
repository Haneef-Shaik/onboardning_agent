"""What the API needs from the audit log: read a page, and make the indexes at startup.

`onb_data.AuditLog` satisfies it in the service; `onb_api.testing.InMemoryAuditLog` in tests.
"""

from __future__ import annotations

from typing import Protocol

from onb_data import AuditPage, AuditQuery

__all__ = ["AuditReader"]


class AuditReader(Protocol):
    async def ensure_indexes(self) -> None:
        """Create the log's indexes (idempotent); run at startup."""

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> AuditPage:
        """One page of the current tenant's records matching `query`, newest first."""
