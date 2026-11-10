"""The tenant of the current request or job (PRD §7.9 Request context).

`org_id` is set only by trusted code: the FastAPI dependency from the signed token (AUTH-1), or an
event consumer from the job (EVT-1). The context is a contextvar, so concurrent requests and
asyncio tasks never see each other's tenant.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .errors import NoTenantContextError

__all__ = ["TenantContext", "current_tenant", "tenant_scope"]

# Letters, digits, `_` and `-` only: no `$` or `.`, so an org id can never act as a query operator
# or a dotted path.
_ORG_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

_current: ContextVar[TenantContext | None] = ContextVar("onb_tenant_context", default=None)


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.org_id, str) or not _ORG_ID.fullmatch(self.org_id):
            raise ValueError("org_id must be 1-128 characters of letters, digits, '_' or '-'")


@contextmanager
def tenant_scope(org_id: str) -> Iterator[TenantContext]:
    """Run the block as `org_id`. The previous context (or none) is restored on exit."""
    ctx = TenantContext(org_id)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def current_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise NoTenantContextError("no tenant context: wrap the call in tenant_scope(org_id)")
    return ctx
