"""The documents and queries the Mongo bus writes, as pure functions (PRD §7.2).

`events`: `{_id, org_id, type, payload, run_id, idempotency_key, traceparent, created_at}`, append-only.
`jobs`: one per subscribed consumer, claimed by lease. `dead_letters`: the job as it was, plus why.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from .models import JobStatus

__all__ = [
    "LEASE_FIELDS",
    "check_name",
    "claim_query",
    "claim_update",
    "complete_update",
    "dead_letter_document",
    "event_document",
    "heartbeat_update",
    "job_document",
    "new_id",
    "retry_update",
]

Document = dict[str, Any]

# Event types and consumer names: dotted lower-case words, e.g. `run.started`, `orchestrator.start_run`.
_NAME = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)*")
_MAX_NAME = 100

LEASE_FIELDS = ("lease_token", "lease_owner", "locked_until")
_RELEASE = {field: "" for field in LEASE_FIELDS}


def check_name(value: object, what: str) -> str:
    if not isinstance(value, str) or len(value) > _MAX_NAME or not _NAME.fullmatch(value):
        raise ValueError(f"{what} must be dotted lower-case words (e.g. 'run.started'), got {value!r}")
    return value


def new_id() -> str:
    """An unguessable id: a caller can never probe another org's ids (T-1 finding)."""
    return str(uuid.uuid4())


def event_document(
    *,
    event_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    run_id: str | None,
    idempotency_key: str,
    traceparent: str | None,
    now: datetime,
) -> Document:
    """`org_id` is added by the tenant repository from the context, never by the caller."""
    return {
        "_id": event_id,
        "type": event_type,
        "payload": dict(payload),
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "traceparent": traceparent,
        "created_at": now,
    }


def job_document(*, event: Mapping[str, Any], consumer: str, max_attempts: int, now: datetime) -> Document:
    return {
        "_id": new_id(),
        "event_id": event["_id"],
        "event_type": event["type"],
        "consumer": consumer,
        "payload": dict(event["payload"]),
        "run_id": event["run_id"],
        "traceparent": event["traceparent"],
        "status": JobStatus.PENDING.value,
        "attempts": 0,
        "max_attempts": max_attempts,
        "available_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }


def claim_query(consumers: Iterable[str], now: datetime) -> Document:
    """Ready jobs of these consumers: pending and due, or running with a lapsed lease (a crashed worker).

    No `org_id` here on purpose: the org is read from the claimed job, and a job without one must
    still be claimed so it can be dead-lettered.
    """
    return {
        "consumer": {"$in": sorted(consumers)},
        "$or": [
            {"status": JobStatus.PENDING.value, "available_at": {"$lte": now}},
            {"status": JobStatus.RUNNING.value, "locked_until": {"$lte": now}},
        ],
    }


def claim_update(*, worker_id: str, lease_token: str, now: datetime, lease: timedelta) -> Document:
    """Take the lease and count the attempt, in the same atomic write as the match."""
    return {
        "$set": {
            "status": JobStatus.RUNNING.value,
            "lease_token": lease_token,
            "lease_owner": worker_id,
            "locked_until": now + lease,
            "claimed_at": now,
            "updated_at": now,
        },
        "$inc": {"attempts": 1},
    }


def heartbeat_update(*, now: datetime, lease: timedelta) -> Document:
    return {"$set": {"locked_until": now + lease, "updated_at": now}}


def complete_update(*, now: datetime) -> Document:
    return {
        "$set": {"status": JobStatus.DONE.value, "completed_at": now, "updated_at": now},
        "$unset": dict(_RELEASE),
    }


def retry_update(*, now: datetime, delay: timedelta, error: str) -> Document:
    return {
        "$set": {
            "status": JobStatus.PENDING.value,
            "available_at": now + delay,
            "last_error": error,
            "updated_at": now,
        },
        "$unset": dict(_RELEASE),
    }


def dead_letter_document(job: Mapping[str, Any], *, reason: str, error: str | None, now: datetime) -> Document:
    """The job as claimed, without its lease, plus why it stopped. A missing `org_id` stays missing."""
    kept = {key: value for key, value in job.items() if key not in LEASE_FIELDS}
    return {
        **kept,
        "status": JobStatus.DEAD.value,
        "reason": reason,
        "last_error": error,
        "dead_lettered_at": now,
        "updated_at": now,
    }
