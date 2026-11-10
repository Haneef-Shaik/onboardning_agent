"""Runtime safety net: a PyMongo command listener that flags unscoped commands (PRD §7.9 Safety net).

`TenantRepository` is the first line: it scopes every query. This listener is the second. It sees
every command any client in the process sends (once installed globally), including clients built
outside `onb_data`, and flags a command on a tenant collection whose filter lacks `org_id`.

PyMongo catches and logs exceptions raised inside a listener, so a listener cannot stop the
command. The listener therefore records each violation and logs it at ERROR, and `check()` raises
`UnscopedCommandError` for what it recorded. The integration test fixture calls `check()` after
every test (tests); staging alerts on the ERROR log line.

A violation never carries values from the command (filters may hold PII tokens or other data);
only the database, collection, command name and a fixed reason.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pymongo import monitoring

from .errors import UnscopedCommandError
from .scoping import CROSS_COLLECTION_STAGES, ORG_FIELD
from .store import GLOBAL_COLLECTIONS

__all__ = [
    "TenantCommandListener",
    "Violation",
    "find_index_violation",
    "find_violation",
    "install_safety_net",
    "is_tenant_collection",
]

_log = logging.getLogger(__name__)

# Server databases that never hold tenant data.
_SYSTEM_DATABASES = frozenset({"admin", "config", "local"})

# command name -> the field that holds its filter.
_FILTER_FIELD = {"find": "filter", "count": "query", "distinct": "query", "findAndModify": "query"}
# command name -> (list field, filter field in each statement).
_STATEMENT_FILTERS = {"update": ("updates", "q"), "delete": ("deletes", "q")}
# Commands that cannot be org-scoped through this layer at all.
_NEVER_SCOPED = frozenset({"mapReduce"})


def is_tenant_collection(database: str, collection: object) -> bool:
    """A collection holding org-owned documents: not global, not a system collection or database."""
    return (
        isinstance(collection, str)
        and bool(collection)
        and database not in _SYSTEM_DATABASES
        and collection not in GLOBAL_COLLECTIONS
        and not collection.startswith("system.")
    )


def _is_org_equality(value: object) -> bool:
    """`org_id: "<org>"` or `org_id: {"$eq": "<org>"}`: one org, nothing else."""
    if isinstance(value, str):
        return bool(value)
    return isinstance(value, Mapping) and set(value) == {"$eq"} and isinstance(value["$eq"], str) and bool(value["$eq"])


def _is_scoped_filter(query: object) -> bool:
    """True when the filter pins one org: top-level `org_id` equality, or an `$and` branch that does.

    `$or`, `$nor`, `$in`, `$ne`, `$exists` and the like never count: they can match other orgs.
    """
    if not isinstance(query, Mapping):
        return False
    if ORG_FIELD in query:
        return _is_org_equality(query[ORG_FIELD])
    branches = query.get("$and")
    return isinstance(branches, list) and any(_is_scoped_filter(branch) for branch in branches)


def _foreign_stage(node: object, database: str) -> str | None:
    """A stage at any depth that reads or writes a tenant collection outside the leading `$match`."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in {"$out", "$merge"}:
                return f"pipeline stage {key} writes another collection"
            if key in CROSS_COLLECTION_STAGES:
                target = value.get("from", value.get("coll")) if isinstance(value, Mapping) else value
                if target is None or is_tenant_collection(database, target):
                    return f"pipeline stage {key} reads a tenant collection outside the org $match"
            found = _foreign_stage(value, database)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _foreign_stage(item, database)
            if found:
                return found
    return None


def _aggregate_violation(command: Mapping[str, Any], database: str) -> str | None:
    pipeline = command.get("pipeline")
    if not isinstance(pipeline, list):
        return "aggregate without a pipeline list"
    foreign = _foreign_stage(pipeline, database)
    if foreign:
        return foreign
    if not is_tenant_collection(database, command.get("aggregate")):
        return None
    first = pipeline[0] if pipeline else None
    if not isinstance(first, Mapping) or not _is_scoped_filter(first.get("$match")):
        return "aggregate does not start with a $match on org_id"
    return None


def find_index_violation(collection: str, key: object) -> str | None:
    """Why an index on a tenant collection is not allowed, or None. `_id` is the only exception."""
    if not isinstance(key, Mapping) or not key:
        return "index key is not a non-empty document"
    fields = list(key)
    if fields == ["_id"]:
        return None
    if fields[0] != ORG_FIELD:
        return f"index on tenant collection {collection} does not start with org_id"
    return None


def _create_indexes_violation(command: Mapping[str, Any], collection: str) -> str | None:
    indexes = command.get("indexes")
    if not isinstance(indexes, list):
        return "createIndexes without an index list"
    for index in indexes:
        reason = find_index_violation(collection, index.get("key") if isinstance(index, Mapping) else None)
        if reason:
            return reason
    return None


def _insert_violation(command: Mapping[str, Any]) -> str | None:
    documents = command.get("documents")
    if not isinstance(documents, list):
        return "insert without a document list"
    if not all(isinstance(doc, Mapping) and _is_org_equality(doc.get(ORG_FIELD)) for doc in documents):
        return "insert of a document without a string org_id"
    return None


def _statements_violation(command: Mapping[str, Any], list_field: str, filter_field: str) -> str | None:
    statements = command.get(list_field)
    if not isinstance(statements, list):
        return f"{list_field} is not a list"
    if not all(isinstance(s, Mapping) and _is_scoped_filter(s.get(filter_field)) for s in statements):
        return f"a statement in {list_field} has no org_id filter"
    return None


def _bulk_write_violation(command: Mapping[str, Any]) -> str | None:
    """Client-level `bulkWrite` (MongoDB 8): refused on any tenant namespace; it is never scoped here."""
    namespaces = command.get("nsInfo")
    if not isinstance(namespaces, list):
        return "bulkWrite without an nsInfo list"
    for entry in namespaces:
        ns = entry.get("ns") if isinstance(entry, Mapping) else None
        if not isinstance(ns, str) or "." not in ns:
            return "bulkWrite with a malformed namespace"
        database, collection = ns.split(".", 1)
        if is_tenant_collection(database, collection):
            return "client bulkWrite on a tenant collection"
    return None


def find_violation(command_name: str, command: Mapping[str, Any], database: str) -> str | None:
    """Why this command breaks tenant scoping, or None. The reason never repeats command values."""
    if command_name == "bulkWrite":
        return _bulk_write_violation(command)
    if command_name == "aggregate":
        return _aggregate_violation(command, database)
    collection = command.get(command_name)
    if not is_tenant_collection(database, collection):
        return None
    if command_name in _NEVER_SCOPED:
        return f"{command_name} cannot be scoped to one org"
    if command_name in _FILTER_FIELD:
        if _is_scoped_filter(command.get(_FILTER_FIELD[command_name])):
            return None
        return f"{command_name} filter has no org_id equality"
    if command_name in _STATEMENT_FILTERS:
        return _statements_violation(command, *_STATEMENT_FILTERS[command_name])
    if command_name == "insert":
        return _insert_violation(command)
    if command_name == "createIndexes":
        return _create_indexes_violation(command, str(collection))
    return None


@dataclass(frozen=True, slots=True)
class Violation:
    database: str
    collection: str
    command: str
    reason: str

    def describe(self) -> str:
        return f"unscoped {self.command} on {self.database}.{self.collection}: {self.reason}"


def _collection_of(command_name: str, command: Mapping[str, Any]) -> str:
    target = command.get(command_name)
    return target if isinstance(target, str) else "*"


class TenantCommandListener(monitoring.CommandListener):
    """Records every unscoped command on a tenant collection. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._violations: list[Violation] = []
        self._allowed: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"onb_unscoped_allowed_{id(self)}", default=None
        )

    @property
    def violations(self) -> tuple[Violation, ...]:
        with self._lock:
            return tuple(self._violations)

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        if self._allowed.get() is not None:
            return
        reason = find_violation(event.command_name, event.command, event.database_name)
        if reason is None:
            return
        violation = Violation(
            database=event.database_name,
            collection=_collection_of(event.command_name, event.command),
            command=event.command_name,
            reason=reason,
        )
        _log.error("tenant safety net: %s", violation.describe())
        with self._lock:
            self._violations.append(violation)

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        return None

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        return None

    def drain(self) -> tuple[Violation, ...]:
        with self._lock:
            drained = tuple(self._violations)
            self._violations.clear()
        return drained

    def check(self) -> None:
        """Raise `UnscopedCommandError` for everything recorded since the last check, then forget it."""
        drained = self.drain()
        if drained:
            lines = "\n".join(f"  - {v.describe()}" for v in drained)
            raise UnscopedCommandError(f"{len(drained)} unscoped command(s) on tenant collections:\n{lines}")

    @contextmanager
    def allowing_unscoped(self, reason: str) -> Iterator[None]:
        """Let this thread or task send unscoped commands inside the block, e.g. a test's root
        read-back that checks isolation from outside. Needs a reason; never for application code."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("allowing unscoped commands needs a reason")
        token = self._allowed.set(reason)
        try:
            yield
        finally:
            self._allowed.reset(token)


_installed: TenantCommandListener | None = None
_install_lock = threading.Lock()


def install_safety_net() -> TenantCommandListener:
    """Register the listener globally, for every client created afterwards. Idempotent.

    Call it before the first client is made (test session start, or staging app startup).
    """
    global _installed  # one process-wide listener, mirroring pymongo's own global registry
    with _install_lock:
        if _installed is None:
            _installed = TenantCommandListener()
            monitoring.register(_installed)
        return _installed
