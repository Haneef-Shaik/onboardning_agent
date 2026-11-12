"""What the bus hands to callers and consumers (PRD §7.2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "DeadLetterReason",
    "Handler",
    "JobContext",
    "JobStatus",
    "Processed",
    "PublishResult",
    "ReleaseJob",
    "WorkOutcome",
]


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"
    """Only in `dead_letters`."""


class DeadLetterReason(StrEnum):
    MISSING_ORG_ID = "missing_org_id"
    INVALID_ORG_ID = "invalid_org_id"
    MALFORMED = "malformed"
    EXHAUSTED = "exhausted"


class ReleaseJob(Exception):  # noqa: N818 - a signal, not an error
    """Raised by a handler to hand its job back: the job becomes claimable again at once and the attempt
    is not counted. For a worker that is draining (SIGTERM): it finished what it could and leaves the
    rest to the next worker, however many times that happens."""


class WorkOutcome(StrEnum):
    COMPLETED = "completed"
    RELEASED = "released"
    """The handler raised `ReleaseJob`: the job is pending again, no attempt used, no backoff."""
    RETRIED = "retried"
    DEAD_LETTERED = "dead_lettered"
    LEASE_LOST = "lease_lost"
    """Another worker took the job over; this worker stopped and wrote nothing."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    event_id: str
    duplicate: bool
    """True when the org already had an event with this idempotency key: nothing was written."""
    job_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JobContext:
    """One job as a consumer sees it. The tenant context is already set to `org_id`."""

    job_id: str
    org_id: str
    consumer: str
    event_id: str
    event_type: str
    payload: Mapping[str, Any]
    """Read-only. Carries ids and `ptk_` tokens only, never raw PII (PRD §5.5)."""
    run_id: str | None
    attempt: int
    """1 on the first claim; a retried or taken-over job sees 2, 3, …"""


@dataclass(frozen=True, slots=True)
class Processed:
    job_id: str
    outcome: WorkOutcome


Handler = Callable[[JobContext], Awaitable[None]]
"""A consumer. Jobs are delivered at least once, so a handler must be idempotent (PRD §12)."""
