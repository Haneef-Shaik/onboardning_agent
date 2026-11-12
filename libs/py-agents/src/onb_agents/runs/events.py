"""The run events and who consumes them (PRD §7.2, §7.9 Event bus).

The API publishes `run.started` when it starts a run; the orchestrator's `orchestrator.start_run`
consumer executes it. Producer and consumer build their bus with `run_bus(store)`, so both sides use
this one subscription map (the outbox writes one job per subscriber at publish time). It is the only
place that constructs the platform's `MongoEventBus`, and the cross-tenant suite enumerates it
(`tools/tests_xt/surface.py`: `PLATFORM_SUBSCRIPTIONS`, `BUS_MODULES`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from onb_contracts.notify import SINK_REJECTED, SINK_REJECTED_CONSUMER
from onb_data import DataStore
from onb_eventbus import MongoEventBus, QueueSettings

__all__ = ["FAIL_TASK", "RUN_STARTED", "SINK_REJECTED", "START_RUN", "SUBSCRIPTIONS", "run_bus", "started_key"]

RUN_STARTED: Final = "run.started"
START_RUN: Final = "orchestrator.start_run"
FAIL_TASK: Final = SINK_REJECTED_CONSUMER
"""`orchestrator.fail_task`: fails the task a sink refused a grant for (`task.sink_rejected`, NOTIF-1)."""
SUBSCRIPTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    RUN_STARTED: (START_RUN,),
    SINK_REJECTED: (FAIL_TASK,),
}


def started_key(run_id: str) -> str:
    """The fixed idempotency key of a run's start: publishing it twice writes it once."""
    return f"run:{run_id}:started"


def run_bus(store: DataStore, settings: QueueSettings | None = None) -> MongoEventBus:
    """The platform's event bus over `store`, with the run subscriptions."""
    return MongoEventBus(store, subscriptions=SUBSCRIPTIONS, settings=settings)
