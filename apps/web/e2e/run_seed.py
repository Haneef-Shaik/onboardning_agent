"""Runs for the Playwright tests (UI-5), in memory, through the real run service and store.

The real `RunService` (start, read, pause/resume/cancel, retry/complete, the SSE stream) over an
in-memory database, with the engineer template published. No orchestrator runs here, so a test plays
its part through `RunDriver`, which moves tasks with the same `RunStore` calls the orchestrator uses
(`move_task`, `move_run`), so the stream replays real history entries.

Test-only: `driver_router` is mounted by `serve_api.py`, which listens on 127.0.0.1 only. Synthetic data.
Each started run gets its own random W3C trace id (the real one comes from the request's span, F-4).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from fastapi import APIRouter
from onb_agents.registry import AgentRegistry
from onb_agents.runs import RunStarter, RunState, RunStore, TaskState
from onb_agents.runs.testing import OutboxStandIn
from onb_api.auth.tokens import Principal
from onb_api.hires.service import HireService
from onb_api.runs import RunService
from onb_api.runs.control import RunControl
from onb_api.templates import RegistryValidatorSource, TemplateLifecycle
from onb_api.testing import InMemoryAuditLog, InMemoryTemplateStore
from onb_api.testing_mongo import memory_store
from onb_contracts.auth import Role
from onb_contracts.runs import RunCreate
from onb_contracts.templates import Template
from onb_data import tenant_scope
from pydantic import BaseModel

ROOT: Final = Path(__file__).resolve().parents[3]
ORG: Final = "org_acme"
HR: Final = Principal(user_id="u_acme_hr1", org_id=ORG, role=Role.HR)
ADMIN: Final = Principal(user_id="u_acme_hradmin", org_id=ORG, role=Role.HR_ADMIN)
TEMPLATE_KEY: Final = "engineer-india"

# The states a task passes through to reach a target, in order (FR-R2).
_PATH: Final = {
    TaskState.READY: (TaskState.READY,),
    TaskState.RUNNING: (TaskState.READY, TaskState.RUNNING),
    TaskState.DONE: (TaskState.READY, TaskState.RUNNING, TaskState.DONE),
    TaskState.FAILED: (TaskState.READY, TaskState.RUNNING, TaskState.FAILED),
}


def _traceparent() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


class RunDriver:
    """What the orchestrator does to a stored run, for tests to play."""

    def __init__(self, store: RunStore, service: RunService) -> None:
        self._store = store
        self._service = service

    async def start(self, hire_id: str) -> dict[str, Any]:
        with tenant_scope(ORG):
            record = await self._service.start(
                HR, RunCreate(hire_id=hire_id, template_key=TEMPLATE_KEY, template_version=1)
            )
            run = await self._store.get(record.id) or {}
            tasks = await self._store.tasks(record.id)
        return {
            "run_id": record.id,
            "trace_id": str(run["traceparent"]).split("-")[1],
            "tasks": [t["task_id"] for t in tasks],
        }

    async def move(self, run_id: str, task_id: str, to: TaskState, error: str | None = None) -> None:
        """Move a task to `to` (through the states between), starting the run with its first task, and
        blocking the run when a task fails its last attempt (ADR 0018 §4)."""
        now = datetime.now(UTC)
        with tenant_scope(ORG):
            run = await self._store.get(run_id) or {}
            if RunState(run["status"]) is RunState.STARTED:
                await self._store.move_run(run_id, RunState.RUNNING, at=now)
            for step in _PATH[to]:
                current = await self._store.task(run_id, task_id) or {}
                if _reached(current["status"], step):
                    continue
                fields = {"error": error} if step is TaskState.FAILED and error else None
                await self._store.move_task(run_id, task_id, step, at=datetime.now(UTC), fields=fields)
            if to is TaskState.FAILED:
                await self._store.move_run(run_id, RunState.BLOCKED, at=datetime.now(UTC))


_RANK: Final = {
    TaskState.PENDING: 0,
    TaskState.READY: 1,
    TaskState.RUNNING: 2,
    TaskState.DONE: 3,
    TaskState.FAILED: 3,
}


def _reached(status: str, step: TaskState) -> bool:
    """The task is already at `step` or beyond it on the way to a target."""
    return _RANK.get(TaskState(status), 3) >= _RANK[step]


class MoveRequest(BaseModel):
    task_id: str
    to: str
    error: str | None = None


class StartRequest(BaseModel):
    hire_id: str


def driver_router(driver: RunDriver) -> APIRouter:
    router = APIRouter(prefix="/e2e", include_in_schema=False)

    @router.post("/runs")
    async def start(body: StartRequest) -> dict[str, Any]:
        return await driver.start(body.hire_id)

    @router.post("/runs/{run_id}/move")
    async def move(run_id: str, body: MoveRequest) -> dict[str, str]:
        await driver.move(run_id, body.task_id, TaskState(body.to), body.error)
        return {"ok": "true"}

    return router


async def run_services(
    registry: AgentRegistry, hires: HireService, auditor: InMemoryAuditLog
) -> tuple[RunService, RunDriver]:
    """The run service (templates published) and the driver over the same in-memory run store."""
    templates = TemplateLifecycle(
        InMemoryTemplateStore(), RegistryValidatorSource.from_config(registry, ROOT / "config"), auditor
    )
    store = memory_store()
    bus = OutboxStandIn()
    run_store = RunStore(store)
    starter = RunStarter(run_store, bus, auditor, traceparent=_traceparent)
    control = RunControl(runs=run_store, bus=bus, auditor=auditor, hires=hires)
    service = RunService(starter=starter, hires=hires, templates=templates, control=control)
    await service.ensure_indexes()
    await templates.ensure_indexes()
    document: dict[str, Any] = yaml.safe_load((ROOT / "templates" / f"{TEMPLATE_KEY}.yaml").read_text())
    with tenant_scope(ORG):
        draft = await templates.create_draft(Template.model_validate(document), actor_id=ADMIN.user_id)
        await templates.publish(draft.id, actor_id=ADMIN.user_id)
    return service, RunDriver(run_store, service)
