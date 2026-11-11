"""Start a run: compile, then write the run, its tasks, the audit record and `run.started` at once
(PRD FR-R1, §7.1 Compilation, §7.2 outbox).

The caller passes a published template version (pinned: the run records its key, version and DAG
hash) and the hire's variables. A template that does not compile raises `CompileError` and writes
nothing. Otherwise the run, one task record per compiled task, the `run.start` audit record and the
event commit in one transaction through the bus's `change` (AUD-1: an action whose audit write fails
is not done). The event's idempotency key is fixed per run (`run:<id>:started`), and its payload holds
ids only. The request's `traceparent` is stored on the run, so the orchestrator's task spans join the
trace that started it (F-4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts.templates import Template
from onb_data import Actor, Auditor, AuditOutcome, current_tenant
from onb_eventbus import EventBus
from onb_telemetry import inject_traceparent

from onb_agents.dag import compile_dag

from .events import RUN_STARTED, started_key
from .records import new_run_id, run_records
from .store import RunStore

__all__ = ["START_ACTION", "RunStarter", "StartedRun", "run_target"]

START_ACTION: Final = "run.start"


def run_target(run_id: str) -> str:
    return f"run:{run_id}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StartedRun:
    run: dict[str, Any]
    tasks: tuple[dict[str, Any], ...]
    event_id: str


class RunStarter:
    def __init__(
        self,
        runs: RunStore,
        bus: EventBus,
        auditor: Auditor,
        *,
        clock: Callable[[], datetime] = _utc_now,
        new_id: Callable[[], str] = new_run_id,
        traceparent: Callable[[], str | None] = inject_traceparent,
    ) -> None:
        self._runs = runs
        self._bus = bus
        self._auditor = auditor
        self._clock = clock
        self._new_id = new_id
        self._traceparent = traceparent

    @property
    def runs(self) -> RunStore:
        return self._runs

    async def ensure_indexes(self) -> None:
        await self._runs.ensure_indexes()

    async def start(
        self, *, template: Template, hire_id: str, variables: Mapping[str, object], actor_id: str
    ) -> StartedRun:
        dag = compile_dag(template, variables)
        run_id = self._new_id()
        records = run_records(
            org_id=current_tenant().org_id,
            run_id=run_id,
            hire_id=hire_id,
            dag=dag,
            actor_id=actor_id,
            traceparent=self._traceparent(),
            now=self._clock(),
        )

        async def change() -> None:
            await self._runs.insert(records)
            await self._auditor.audit(
                actor=Actor.user(actor_id),
                action=START_ACTION,
                target=run_target(run_id),
                outcome=AuditOutcome.SUCCESS,
            )

        published = await self._bus.publish(
            RUN_STARTED, {"run_id": run_id}, idempotency_key=started_key(run_id), run_id=run_id, change=change
        )
        return StartedRun(run=records.run, tasks=records.tasks, event_id=published.event_id)
