"""Shared helpers for the event bus unit tests: a settable clock and a bus over the memory double."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from onb_eventbus import MongoEventBus, QueueSettings

from .memory_mongo import MemoryDatabase, memory_store

ORG_A = "org_alpha"
ORG_B = "org_beta"
CONSUMER = "orchestrator.start_run"
EVENT = "run.started"

FAST = QueueSettings(
    lease_seconds=30.0,
    heartbeat_seconds=0.01,
    max_attempts=3,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
    poll_seconds=0.01,
)


class Clock:
    """A clock the test moves by hand, so lease expiry needs no sleeping."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_bus(
    settings: QueueSettings = FAST, subscriptions: dict[str, list[str]] | None = None
) -> tuple[MongoEventBus, MemoryDatabase, Clock]:
    store, db, _ = memory_store()
    clock = Clock()
    bus = MongoEventBus(
        store,
        subscriptions=subscriptions if subscriptions is not None else {EVENT: [CONSUMER]},
        settings=settings,
        clock=clock,
    )
    return bus, db, clock
