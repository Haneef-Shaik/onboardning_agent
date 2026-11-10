"""The MongoDB event bus (PRD §7.2, §7.9 Event bus, §12 Durability).

Producers publish inside the tenant context; the domain change, the event and one job per subscribed
consumer commit in one transaction, and a repeated idempotency key is ignored. Workers claim jobs by
lease, set the tenant context from the job, continue the producer's trace, retry with exponential
backoff, and move exhausted or org-less jobs to `dead_letters`. Delivery is at least once, so
handlers must be idempotent.
"""

from .bus import Clock, MongoEventBus
from .interface import EventBus, Worker
from .models import (
    DeadLetterReason,
    Handler,
    JobContext,
    JobStatus,
    Processed,
    PublishResult,
    WorkOutcome,
)
from .settings import QueueSettings, backoff_delay
from .worker import MongoWorker

__all__ = [
    "PROJECT",
    "Clock",
    "DeadLetterReason",
    "EventBus",
    "Handler",
    "JobContext",
    "JobStatus",
    "MongoEventBus",
    "MongoWorker",
    "Processed",
    "PublishResult",
    "QueueSettings",
    "WorkOutcome",
    "Worker",
    "backoff_delay",
]

PROJECT = "py-eventbus"
