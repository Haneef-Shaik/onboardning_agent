"""The run service behind `POST /runs` (PRD FR-R1, §7.1 Compilation; RUN-1).

It reads the hire and the requested template version in the caller's org (another org's, or a
missing one, is 404), and hands both to `RunStarter`, which compiles the DAG and writes the run, its
tasks, the audit record and `run.started` in one outbox transaction. The hire gives only its template
variables (`role`, `location`, `start_date`, `manager_id`); its PII tokens never reach a run. A version
that does not compile for this hire, archived ones included, is 422 naming the compiler's reason.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from onb_agents.dag import CompileError
from onb_agents.runs import RunStarter, RunStore, StartedRun, hire_variables
from onb_contracts import ErrorCode, ErrorDetail
from onb_contracts.runs import RunCreate, RunRecord, RunTaskRecord
from onb_contracts.templates import Template
from onb_data import Auditor, DataStore, NotFoundError
from onb_eventbus import EventBus

from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.hires.service import HireService
from onb_api.templates import TemplateLifecycle

__all__ = ["RunService"]


class RunService:
    def __init__(
        self,
        *,
        starter: RunStarter,
        hires: HireService,
        templates: TemplateLifecycle,
        bus_indexes: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._starter = starter
        self._hires = hires
        self._templates = templates
        self._bus_indexes = bus_indexes

    @classmethod
    def build(
        cls,
        store: DataStore,
        bus: EventBus,
        *,
        auditor: Auditor,
        hires: HireService,
        templates: TemplateLifecycle,
        bus_indexes: Callable[[], Awaitable[None]] | None = None,
    ) -> RunService:
        """`bus_indexes` makes the outbox's indexes (the per-org unique idempotency key) at startup:
        `MongoEventBus.ensure_indexes` in production."""
        starter = RunStarter(RunStore(store), bus, auditor)
        return cls(starter=starter, hires=hires, templates=templates, bus_indexes=bus_indexes)

    async def ensure_indexes(self) -> None:
        await self._starter.ensure_indexes()
        if self._bus_indexes is not None:
            await self._bus_indexes()

    async def start(self, principal: Principal, request: RunCreate) -> RunRecord:
        hire = await self._hires.store.get(request.hire_id)
        if hire is None:
            raise AppError(ErrorCode.NOT_FOUND)
        template = await self._version(request.template_key, request.template_version)
        try:
            started = await self._starter.start(
                template=template, hire_id=request.hire_id, variables=hire_variables(hire), actor_id=principal.user_id
            )
        except CompileError as error:
            field = f"tasks.{error.task_id}" if error.task_id else "template"
            detail = ErrorDetail(field=field, issue=str(error))  # `<reason>: <message>`, no values
            raise AppError(
                ErrorCode.VALIDATION_ERROR, "The template version cannot start a run for this hire", details=[detail]
            ) from None
        return _record(started)

    async def _version(self, key: str, version: int) -> Template:
        try:
            record = await self._templates.get_version(key, version)
        except NotFoundError:
            raise AppError(ErrorCode.NOT_FOUND) from None
        return record.template


def _record(started: StartedRun) -> RunRecord:
    run: dict[str, Any] = started.run
    return RunRecord(
        id=run["_id"],
        hire_id=run["hire_id"],
        template_key=run["template_key"],
        template_version=run["template_version"],
        dag_hash=run["dag_hash"],
        status=run["status"],
        created_by=run["created_by"],
        created_at=run["created_at"],
        tasks=[
            RunTaskRecord(
                task_id=task["task_id"],
                title=task["title"],
                type=task["type"],
                agent=task["agent"],
                action=task["action"],
                depends_on=task["depends_on"],
                status=task["status"],
                skip_reason=task["skip_reason"],
                due=task["due"],
            )
            for task in started.tasks
        ],
    )
