"""The worker queue's deliberate cross-org commands, as the safety net allows them (ADR 0002; T-2).

A worker claims a job before it knows the job's org (EVT-1), so `QueueRepository` sends a few
commands on `jobs` and `dead_letters` that carry no `org_id`. The safety net allows exactly those,
matched by collection and command shape, and nothing else on those collections:

- the claim: `findAndModify` on `jobs`, filtering on queue state only, updating queue state only;
- leased writes: one-statement `update`/`delete` on `jobs` or `dead_letters` whose filter is exactly
  `{_id, lease_token}` (no multi, no upsert; a delete removes one document);
- the orphan dead letter: an `insert` into `dead_letters` of a document that has lost its org on
  purpose (`status: "dead"`, `reason: "missing_org_id" | "invalid_org_id"`), PRD §7.9 Event bus.

Everything else on the queue collections goes through the normal tenant checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .queue import CLAIM_FILTER_FIELDS, DEAD_LETTERS, QUEUE_COLLECTIONS, WORKER_STATE_FIELDS
from .scoping import ORG_FIELD

__all__ = ["ORPHAN_REASONS", "queue_command_allowed"]

JOBS = "jobs"
ORPHAN_REASONS = frozenset({"missing_org_id", "invalid_org_id"})
_COMBINATORS = frozenset({"$or", "$and"})
_WORKER_OPERATORS: dict[str, frozenset[str]] = {
    "$set": WORKER_STATE_FIELDS,
    "$unset": WORKER_STATE_FIELDS,
    "$inc": frozenset({"attempts"}),
}


def _claim_filter_ok(node: object) -> bool:
    """Queue-state fields only, combined with `$or`/`$and`: never `org_id`, an id or job content."""
    if not isinstance(node, Mapping) or not node:
        return False
    for key, value in node.items():
        if key in _COMBINATORS:
            if not isinstance(value, list) or not value or not all(_claim_filter_ok(b) for b in value):
                return False
        elif key not in CLAIM_FILTER_FIELDS:
            return False
    return True


def _worker_update_ok(update: object) -> bool:
    """An operator update that changes queue state only: never `org_id`, `consumer` or `payload`."""
    if not isinstance(update, Mapping) or not update:
        return False
    for op, fields in update.items():
        allowed = _WORKER_OPERATORS.get(op)
        if allowed is None or not isinstance(fields, Mapping) or not fields or not set(fields) <= allowed:
            return False
    return True


def _claim_ok(command: Mapping[str, Any], collection: str) -> bool:
    return (
        collection == JOBS
        and not command.get("remove")
        and not command.get("upsert")
        and _claim_filter_ok(command.get("query"))
        and _worker_update_ok(command.get("update"))
    )


def _leased_filter_ok(query: object) -> bool:
    if not isinstance(query, Mapping) or set(query) != {"_id", "lease_token"}:
        return False
    token, job_id = query["lease_token"], query["_id"]
    return isinstance(token, str) and bool(token) and not isinstance(job_id, Mapping | list)


def _leased_statements_ok(command: Mapping[str, Any], list_field: str) -> bool:
    statements = command.get(list_field)
    if not isinstance(statements, list) or len(statements) != 1 or not isinstance(statements[0], Mapping):
        return False
    statement = statements[0]
    if not _leased_filter_ok(statement.get("q")):
        return False
    if list_field == "deletes":
        return statement.get("limit") == 1
    return not statement.get("multi") and not statement.get("upsert") and _worker_update_ok(statement.get("u"))


def _is_orphan_letter(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    org = document.get(ORG_FIELD)
    if isinstance(org, str) and org:
        return True  # an ordinary dead letter, still in its org
    reason = document.get("reason")
    return document.get("status") == "dead" and isinstance(reason, str) and reason in ORPHAN_REASONS


def _dead_letter_insert_ok(command: Mapping[str, Any], collection: str) -> bool:
    documents = command.get("documents")
    return (
        collection == DEAD_LETTERS
        and isinstance(documents, list)
        and bool(documents)
        and all(_is_orphan_letter(d) for d in documents)
    )


def queue_command_allowed(command_name: str, command: Mapping[str, Any], collection: str) -> bool:
    """True only for the exact worker-queue shapes above, on `jobs`/`dead_letters`."""
    if collection not in QUEUE_COLLECTIONS:
        return False
    if command_name == "findAndModify":
        return _claim_ok(command, collection)
    if command_name == "update":
        return _leased_statements_ok(command, "updates")
    if command_name == "delete":
        return _leased_statements_ok(command, "deletes")
    if command_name == "insert":
        return _dead_letter_insert_ok(command, collection)
    return False
