"""The MongoDB event bus: outbox publishing and worker creation (PRD §7.2, §7.9 Event bus, §12).

bus = MongoEventBus(store, subscriptions={"run.started": ["orchestrator.start_run"]})
with tenant_scope(org_id_from_token):
    await bus.publish("run.started", {"run_id": rid}, idempotency_key=f"run:{rid}:started",
                      run_id=rid, change=create_run)      # one transaction
await bus.worker({"orchestrator.start_run": start_run}).run(stop)
"""

from __future__ import annotations

import os
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from onb_data import DataStore, current_tenant
from onb_telemetry import inject_traceparent, span_attributes
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from pymongo.errors import DuplicateKeyError

from . import documents
from .interface import EventBus
from .models import Handler, PublishResult
from .settings import QueueSettings
from .worker import MongoWorker

__all__ = ["Clock", "MongoEventBus"]

Clock = Callable[[], datetime]

_MAX_KEY = 200
_MAX_RUN_ID = 128
_TRACER_NAME = "onb_eventbus"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _subscriptions(raw: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise ValueError("subscriptions must map an event type to its consumers")
    checked: dict[str, tuple[str, ...]] = {}
    for event_type, consumers in raw.items():
        documents.check_name(event_type, "an event type")
        if isinstance(consumers, str) or not isinstance(consumers, Sequence):
            raise ValueError(f"the consumers of {event_type} must be a list of names")
        names = tuple(documents.check_name(c, "a consumer name") for c in consumers)
        if len(set(names)) != len(names):
            raise ValueError(f"{event_type} lists a consumer twice")
        checked[event_type] = names
    return checked


def _publish_args(payload: object, idempotency_key: object, run_id: object) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError("an event payload must be a mapping")
    if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= _MAX_KEY:
        raise ValueError(f"idempotency_key must be a string of 1-{_MAX_KEY} characters")
    if run_id is not None and (not isinstance(run_id, str) or not 0 < len(run_id) <= _MAX_RUN_ID):
        raise ValueError(f"run_id must be a string of 1-{_MAX_RUN_ID} characters")


class MongoEventBus(EventBus):
    def __init__(
        self,
        store: DataStore,
        *,
        subscriptions: Mapping[str, Sequence[str]],
        settings: QueueSettings | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._store = store
        self._subscriptions = _subscriptions(subscriptions)
        self._settings = settings if settings is not None else QueueSettings()
        self._clock = clock

    @property
    def store(self) -> DataStore:
        return self._store

    def consumers(self) -> tuple[str, ...]:
        return tuple(sorted({c for names in self._subscriptions.values() for c in names}))

    async def ensure_indexes(self) -> None:
        store = self._store
        await store.ensure_index(
            "events", [("org_id", 1), ("idempotency_key", 1)], unique=True, name="org_idempotency_key"
        )
        await store.ensure_index("events", [("org_id", 1), ("run_id", 1), ("created_at", 1)])
        await store.ensure_index("jobs", [("status", 1), ("consumer", 1), ("available_at", 1)])
        await store.ensure_index("jobs", [("status", 1), ("consumer", 1), ("locked_until", 1)])
        await store.ensure_index("dead_letters", [("org_id", 1), ("dead_lettered_at", 1)])

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        run_id: str | None = None,
        change: Callable[[], Awaitable[None]] | None = None,
    ) -> PublishResult:
        """Write `change`, the event and one job per subscribed consumer in one transaction.

        The org comes from the tenant context only. If the org already has an event with this
        idempotency key, nothing is written (`change` included) and the existing event id is returned.
        `change` may run more than once if the driver retries a transient error, so it must only
        write through the store. Any error rolls everything back and propagates.
        """
        documents.check_name(event_type, "an event type")
        _publish_args(payload, idempotency_key, run_id)
        org_id = current_tenant().org_id
        attributes = {
            **span_attributes(org_id=org_id, run_id=run_id),
            "messaging.system": "mongodb",
            "messaging.operation.type": "send",
            "messaging.destination.name": event_type,
        }
        tracer = trace.get_tracer(_TRACER_NAME)  # per call: follows the provider in use now
        with tracer.start_as_current_span(f"publish {event_type}", kind=SpanKind.PRODUCER, attributes=attributes):
            traceparent = inject_traceparent()
            try:
                return await self._store.run_transaction(
                    lambda: self._write(event_type, payload, idempotency_key, run_id, traceparent, change)
                )
            except DuplicateKeyError:
                # A concurrent publish with the same key committed first.
                existing = await self._store.tenant("events").find_one(
                    {"idempotency_key": idempotency_key}, projection={"_id": 1}
                )
                if existing is None:
                    raise
                return PublishResult(event_id=existing["_id"], duplicate=True)

    async def _write(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        run_id: str | None,
        traceparent: str | None,
        change: Callable[[], Awaitable[None]] | None,
    ) -> PublishResult:
        events = self._store.tenant("events")
        existing = await events.find_one({"idempotency_key": idempotency_key}, projection={"_id": 1})
        if existing is not None:
            return PublishResult(event_id=existing["_id"], duplicate=True)
        if change is not None:
            await change()
        now = self._clock()
        event = documents.event_document(
            event_id=documents.new_id(),
            event_type=event_type,
            payload=payload,
            run_id=run_id,
            idempotency_key=idempotency_key,
            traceparent=traceparent,
            now=now,
        )
        await events.insert_one(event)
        jobs = [
            documents.job_document(event=event, consumer=consumer, max_attempts=self._settings.max_attempts, now=now)
            for consumer in self._subscriptions.get(event_type, ())
        ]
        if jobs:
            await self._store.tenant("jobs").insert_many(jobs)
        return PublishResult(event_id=event["_id"], duplicate=False, job_ids=tuple(job["_id"] for job in jobs))

    def worker(self, handlers: Mapping[str, Handler], *, worker_id: str | None = None) -> MongoWorker:
        if not handlers:
            raise ValueError("a worker needs at least one handler: no handlers given")
        subscribed = set(self.consumers())
        for consumer, handler in handlers.items():
            if consumer not in subscribed:
                raise ValueError(f"{consumer!r} is not subscribed to any event type")
            if not callable(handler):
                raise TypeError(f"the handler for {consumer} is not callable")
        name = worker_id if worker_id is not None else f"{socket.gethostname()}-{os.getpid()}-{documents.new_id()[:8]}"
        return MongoWorker(self._store, dict(handlers), settings=self._settings, clock=self._clock, worker_id=name)
