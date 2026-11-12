"""The approval SLA sweep (PRD FR-H4; HIL-2, ADR 0027): remind the approver once, then escalate to HR once.

Every `check_interval_seconds` the orchestrator worker looks at each org's pending, not yet escalated
approvals (the orgs come from `orgs`; the tenant scope is set per org, and every read and write goes through
`TenantRepository`). For each stage `due_stages` says is owed, it publishes `approval.overdue
{approval_id, run_id, task_id, stage, escalate_to}` and, **in the same outbox transaction**, stamps the stage
on the approval (`reminded_at` / `escalated_at`) with a compare-and-set that applies only while the approval is
pending and the stage is unstamped. So:

* a stage is sent at most once per approval: the stamp is on the record, not in the worker's memory, so a
  restarted (or a second) worker finds it done; the event's fixed key `approval:<id>:<stage>` is a second stop;
* a decision that lands first wins: the compare-and-set fails, the transaction rolls back and nothing is sent;
* a crash before the commit leaves neither the stamp nor the event, so the next pass sends it.

`notify.approval_sla` (mcp-notify) delivers the reminder to the approver and the escalation to the
`escalate_to` users, in-app and on the Telegram mock. The clock and the sleep are parameters: tests never wait.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from onb_agents.approvals import ApprovalStore
from onb_agents.approvals.sla import SlaPolicy, SlaStage, due_stages
from onb_contracts.notify import APPROVAL_OVERDUE
from onb_data import DataStore, tenant_scope
from onb_eventbus import EventBus

__all__ = ["ORGS_COLLECTION", "ApprovalSlaJob", "SlaReport", "overdue_key", "run_sla_forever"]

log = logging.getLogger(__name__)

ORGS_COLLECTION: Final = "orgs"
_BATCH: Final = 500  # approvals per org per pass; the rest wait for the next pass (oldest first)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def overdue_key(approval_id: str, stage: SlaStage) -> str:
    """The idempotency key of an approval's one event for a stage."""
    return f"approval:{approval_id}:{stage}"


@dataclass(frozen=True, slots=True)
class SlaReport:
    """What one pass sent: reminders, escalations, and orgs whose pass failed (retried next pass)."""

    reminders: int = 0
    escalations: int = 0
    failures: int = 0

    def plus(self, other: SlaReport) -> SlaReport:
        return SlaReport(
            self.reminders + other.reminders, self.escalations + other.escalations, self.failures + other.failures
        )


class _StageTakenError(Exception):
    """The stamp's compare-and-set found the stage done or the approval decided: roll the publish back."""


class ApprovalSlaJob:
    def __init__(
        self,
        store: DataStore,
        bus: EventBus,
        policy: SlaPolicy,
        *,
        clock: Callable[[], datetime] = _utc_now,
        batch: int = _BATCH,
    ) -> None:
        self._store = store
        self._bus = bus
        self._policy = policy
        self._clock = clock
        self._batch = batch
        self._approvals = ApprovalStore(store)

    async def run_once(self) -> SlaReport:
        """One pass over every org. One org's failure is logged and counted; the others still run."""
        report = SlaReport()
        for org in await self._store.shared(ORGS_COLLECTION).find({}, projection={"_id": 1}):
            org_id = str(org["_id"])
            try:
                with tenant_scope(org_id):
                    report = report.plus(await self._sweep_org())
            except Exception as error:  # one org must not stop the sweep; the next pass retries it
                log.error("approval sla pass failed for an org: %s", type(error).__name__)
                report = report.plus(SlaReport(failures=1))
        return report

    async def _sweep_org(self) -> SlaReport:
        now = self._clock()
        report = SlaReport()
        for approval in await self._approvals.awaiting_sla(limit=self._batch):
            for stage in due_stages(approval, now, self._policy):
                if not await self._send(approval, stage, now):
                    break  # a decision (or another worker) got there first: nothing more is owed
                report = report.plus(SlaReport(reminders=1) if stage == "reminder" else SlaReport(escalations=1))
        return report

    async def _send(self, approval: dict[str, Any], stage: SlaStage, now: datetime) -> bool:
        approval_id = str(approval["_id"])

        async def stamp() -> None:
            if not await self._approvals.mark_stage(approval_id, stage, at=now):
                raise _StageTakenError

        payload = {
            "approval_id": approval_id,
            "run_id": str(approval["run_id"]),
            "task_id": str(approval["task_id"]),
            "stage": stage,
            "escalate_to": self._policy.escalate_to_role if stage == "escalation" else None,
        }
        try:
            published = await self._bus.publish(
                APPROVAL_OVERDUE,
                payload,
                idempotency_key=overdue_key(approval_id, stage),
                run_id=str(approval["run_id"]),
                change=stamp,
            )
        except _StageTakenError:
            return False
        return not published.duplicate


class SlaPass(Protocol):
    async def run_once(self) -> SlaReport: ...


async def _wait(sleeping: Awaitable[None], stop: asyncio.Event) -> None:
    """Wait out the interval, or return at once when `stop` is set."""
    waiting = asyncio.ensure_future(sleeping)
    stopped = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({waiting, stopped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (waiting, stopped):
            task.cancel()
        await asyncio.gather(waiting, stopped, return_exceptions=True)


async def run_sla_forever(
    job: SlaPass,
    *,
    interval: float,
    stop: asyncio.Event,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[SlaReport]:
    """A pass every `interval` seconds until `stop`. A pass that raises is logged; the loop goes on."""
    reports: list[SlaReport] = []
    while not stop.is_set():
        try:
            reports.append(await job.run_once())
        except Exception as error:
            log.error("approval sla pass failed: %s", type(error).__name__)
        if stop.is_set():
            break
        await _wait(sleep(interval), stop)
    return reports
