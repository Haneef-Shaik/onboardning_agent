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

from onb_contracts.notify import IN_APP_CONSUMER, RUN_BLOCKED, SINK_REJECTED, SINK_REJECTED_CONSUMER
from onb_data import DataStore
from onb_eventbus import MongoEventBus, QueueSettings

__all__ = [
    "CLEANUP_RUN",
    "FAIL_TASK",
    "RESUME_RUN",
    "RUN_BLOCKED_KIND",
    "RUN_CANCELLED",
    "RUN_PAUSED",
    "RUN_RESUMED",
    "RUN_STARTED",
    "SINK_REJECTED",
    "START_RUN",
    "SUBSCRIPTIONS",
    "control_key",
    "run_bus",
    "started_key",
]

RUN_STARTED: Final = "run.started"
START_RUN: Final = "orchestrator.start_run"
FAIL_TASK: Final = SINK_REJECTED_CONSUMER
"""`orchestrator.fail_task`: fails the task a sink refused a grant for (`task.sink_rejected`, NOTIF-1)."""
RUN_RESUMED: Final = "run.resumed"
"""Published when HR resumes a paused run, retries a failed task or completes one by hand (RUN-2):
`{run_id}`. The orchestrator drives the run on from where it stopped."""
RESUME_RUN: Final = "orchestrator.resume_run"
RUN_PAUSED: Final = "run.paused"
"""Published when HR pauses a run (RUN-2): `{run_id}`. Nobody consumes it: it is the audit trail's event."""
RUN_BLOCKED_KIND: Final = "blocked"
"""The `kind` of a blocked run's idempotency key (`control_key`); the event itself is NOTIF-1's `run.blocked`."""
RUN_CANCELLED: Final = "run.cancelled"
"""Published when HR cancels a run (RUN-2): `{run_id}`. The orchestrator deletes the run's checkpoints."""
CLEANUP_RUN: Final = "orchestrator.cleanup_run"
SUBSCRIPTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    RUN_STARTED: (START_RUN,),
    RUN_RESUMED: (RESUME_RUN,),
    RUN_CANCELLED: (CLEANUP_RUN,),
    # A blocked run tells the org's HR in-app (NOTIF-1 owns the consumer, RUN-2 the producer).
    RUN_BLOCKED: (IN_APP_CONSUMER,),
    # RUN-3: a sink's refusal fails the task it was for.
    SINK_REJECTED: (FAIL_TASK,),
}


def control_key(run_id: str, kind: str, sequence: int) -> str:
    """The idempotency key of one control event of a run: `sequence` is the length of the run's history
    before the move that publishes it, so a redelivered request publishes once and a later one again."""
    return f"run:{run_id}:{kind}:{sequence}"


def started_key(run_id: str) -> str:
    """The fixed idempotency key of a run's start: publishing it twice writes it once."""
    return f"run:{run_id}:started"


def run_bus(store: DataStore, settings: QueueSettings | None = None) -> MongoEventBus:
    """The platform's event bus over `store`, with the run subscriptions."""
    return MongoEventBus(store, subscriptions=SUBSCRIPTIONS, settings=settings)
