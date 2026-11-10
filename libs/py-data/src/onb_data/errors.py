"""Errors raised by the data layer (PRD §7.9)."""

from __future__ import annotations

__all__ = [
    "DataAccessError",
    "InvalidIndexError",
    "NoTenantContextError",
    "NotFound",
    "NotFoundError",
    "TenantViolationError",
    "TransactionError",
    "UnknownCollectionError",
    "UnscopedCommandError",
]


class DataAccessError(Exception):
    """Base class for every error the data layer raises on its own."""


class NoTenantContextError(DataAccessError):
    """A tenant repository was used outside `tenant_scope(...)`. The API maps this to 401."""


class NotFoundError(DataAccessError):
    """No document with that id in the caller's org.

    A document that exists in another org is reported the same way, so callers cannot tell the two
    apart (PRD §7.9 IDs: a foreign id is 404, never 403). Deliberately not a `PermissionError`.
    """


# The name the T-1 card and the API layer use; the same class.
NotFound = NotFoundError


class TenantViolationError(DataAccessError):
    """The caller tried to choose, change or step around the tenant scope."""


class InvalidIndexError(DataAccessError):
    """An index spec is malformed, or a tenant index does not start with `org_id`."""


class UnknownCollectionError(DataAccessError):
    """A global collection was asked for as a tenant one, or the other way round."""


class TransactionError(DataAccessError):
    """A transaction was nested, or the store has no client to start one."""


class UnscopedCommandError(DataAccessError):
    """The safety net saw a command on a tenant collection without an `org_id` scope (PRD §7.9)."""
