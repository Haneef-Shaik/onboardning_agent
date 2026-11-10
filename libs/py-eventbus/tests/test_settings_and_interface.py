"""Queue settings, the backoff curve and the `EventBus` interface (PRD §7.2)."""

from __future__ import annotations

from typing import Any

import pytest
from onb_eventbus import PROJECT, EventBus, MongoEventBus, QueueSettings, backoff_delay

from .helpers import CONSUMER, EVENT, make_bus


def test_package_names_its_project() -> None:
    assert PROJECT == "py-eventbus"


def test_backoff_doubles_from_the_base_and_stops_at_the_cap() -> None:
    delays = [backoff_delay(attempt, base_seconds=1.0, max_seconds=10.0) for attempt in range(1, 7)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_backoff_survives_a_huge_attempt_count() -> None:
    assert backoff_delay(10_000, base_seconds=1.0, max_seconds=300.0) == 300.0


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5])
def test_backoff_needs_a_positive_attempt(attempt: Any) -> None:
    with pytest.raises(ValueError, match="attempt"):
        backoff_delay(attempt, base_seconds=1.0, max_seconds=2.0)


def test_default_settings_are_valid() -> None:
    settings = QueueSettings()
    assert settings.heartbeat_seconds < settings.lease_seconds
    assert settings.max_attempts >= 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lease_seconds": 0},
        {"heartbeat_seconds": 0},
        {"lease_seconds": 5, "heartbeat_seconds": 5},
        {"lease_seconds": 30, "heartbeat_seconds": 29},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"backoff_base_seconds": -1},
        {"backoff_base_seconds": 10, "backoff_max_seconds": 5},
        {"poll_seconds": 0},
        {"lease_seconds": float("nan")},
    ],
    ids=str,
)
def test_invalid_settings_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"seconds|attempts"):
        QueueSettings(**kwargs)


def test_the_mongo_bus_is_an_event_bus() -> None:
    bus, _, _ = make_bus()
    assert isinstance(bus, EventBus)
    assert issubclass(MongoEventBus, EventBus)


def test_the_bus_lists_its_consumers_for_the_cross_tenant_suite() -> None:
    bus, _, _ = make_bus(subscriptions={EVENT: [CONSUMER, "notify.welcome"], "run.done": ["notify.welcome"]})
    assert bus.consumers() == ("notify.welcome", CONSUMER)


@pytest.mark.parametrize(
    "subscriptions",
    [{"": ["c"]}, {"e": [""]}, {"e": ["c", "c"]}, {"e": "c"}, {"e": ["has space"]}, {"$e": ["c"]}],
    ids=str,
)
def test_bad_subscriptions_are_refused(subscriptions: Any) -> None:
    with pytest.raises(ValueError, match=r"event type|consumer"):
        make_bus(subscriptions=subscriptions)


def test_a_worker_needs_handlers_for_subscribed_consumers_only() -> None:
    bus, _, _ = make_bus()

    async def handler(job: object) -> None:
        return None

    with pytest.raises(ValueError, match="no handlers"):
        bus.worker({})
    with pytest.raises(ValueError, match="not subscribed"):
        bus.worker({"unknown.consumer": handler})
    assert bus.worker({CONSUMER: handler}, worker_id="w1").worker_id == "w1"


def test_indexes_make_the_idempotency_key_unique_per_org_and_support_the_claim() -> None:
    import asyncio

    bus, db, _ = make_bus()
    asyncio.run(bus.ensure_indexes())
    by_collection = {(name, tuple(k for k, _ in keys), unique) for name, keys, unique in db.indexes}
    assert ("events", ("org_id", "idempotency_key"), True) in by_collection
    assert ("jobs", ("status", "consumer", "available_at"), False) in by_collection
    assert ("jobs", ("status", "consumer", "locked_until"), False) in by_collection
