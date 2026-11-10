"""The `EventBus` interface, so Kafka or NATS can replace MongoDB later (PRD §7.2)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .models import Handler, Processed, PublishResult

__all__ = ["EventBus", "Worker"]


class Worker(ABC):
    worker_id: str

    @abstractmethod
    async def run_once(self) -> Processed | None:
        """Claim and process one job. `None` when no job was ready."""

    @abstractmethod
    async def run(self, stop: asyncio.Event) -> int:
        """Process jobs until `stop` is set. Returns how many were processed."""


class EventBus(ABC):
    @abstractmethod
    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        run_id: str | None = None,
        change: Callable[[], Awaitable[None]] | None = None,
    ) -> PublishResult:
        """Write `change`, the event and its jobs atomically, for the org in the tenant context."""

    @abstractmethod
    def worker(self, handlers: Mapping[str, Handler], *, worker_id: str | None = None) -> Worker:
        """A worker for the given consumers."""

    @abstractmethod
    def consumers(self) -> tuple[str, ...]:
        """Every subscribed consumer, sorted. The cross-tenant suite (T-3) needs a case for each."""
