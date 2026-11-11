"""What the sink publishes through the EVT-1 bus (outbox pattern: the write and its event commit together).

* `notification.sent` with the `mock_outbox` insert as the domain change, in one transaction.
* `task.sink_rejected` when a grant is refused: the orchestrator fails the task (RUN-3 subscribes).

Payloads hold ids, tokens and codes only (EVT-1 contract).
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from onb_contracts.notify import NOTIFICATION_SENT, SINK_REJECTED
from onb_eventbus import MongoEventBus

__all__ = ["BusEvents", "NotifyEvents"]

Change = Callable[[], Awaitable[None]]


class NotifyEvents(Protocol):
    async def sent(self, payload: Mapping[str, Any], *, key: str, run_id: str | None, change: Change) -> None:
        """Run `change` (the outbox write) and publish `notification.sent`, atomically."""

    async def rejected(self, payload: Mapping[str, Any], *, run_id: str) -> None:
        """Publish `task.sink_rejected`."""


class BusEvents:
    """`NotifyEvents` over a `MongoEventBus`. Call inside the caller's tenant scope."""

    def __init__(self, bus: MongoEventBus) -> None:
        self._bus = bus

    async def sent(self, payload: Mapping[str, Any], *, key: str, run_id: str | None, change: Change) -> None:
        await self._bus.publish(NOTIFICATION_SENT, payload, idempotency_key=key, run_id=run_id, change=change)

    async def rejected(self, payload: Mapping[str, Any], *, run_id: str) -> None:
        # Every refusal is its own failure report: a fresh key, never folded into an earlier one.
        key = f"notify:rejected:{secrets.token_hex(16)}"
        await self._bus.publish(SINK_REJECTED, payload, idempotency_key=key, run_id=run_id)
