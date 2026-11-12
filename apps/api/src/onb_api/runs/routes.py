"""Runs over HTTP (PRD FR-R1, FR-R4, FR-R5, §10 Hires & runs, Live updates; RUN-1, RUN-2).

| Route | Roles |
|---|---|
| `POST /runs` | HR, HR_ADMIN |
| `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/stream` | any signed-in role; a MANAGER only for hires they manage |
| `POST /runs/{id}/{pause,resume,cancel}` | HR, HR_ADMIN |
| `POST /runs/{id}/tasks/{task_id}/{retry,complete}` | HR, HR_ADMIN |

The org is the token's. The hire and the template version are read in that org only, so another
org's hire or version is 404, exactly like one that does not exist; so is another org's run, and (for a
manager) a run of a hire someone else manages. The started run (201) is pinned to the requested
version: its key, version and DAG hash never change. Controls answer 200 with the run as it is after
the change, 409 when the run's or task's state does not allow it. `complete` needs a reason (422
without). The stream is `text/event-stream`: see `onb_api.runs.stream`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse
from onb_agents.runs import RunState
from onb_contracts import Envelope, ErrorCode, PageMeta
from onb_contracts.auth import Role
from onb_contracts.runs import (
    TASK_ID_PATTERN,
    RunControlRequest,
    RunCreate,
    RunEnvelope,
    RunRecord,
    RunsEnvelope,
    RunStatus,
    RunSummary,
    TaskCompleteRequest,
)

from onb_api.auth.dependencies import CurrentPrincipal, require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond
from onb_api.runs.control import RunControl
from onb_api.runs.service import RunService
from onb_api.runs.stream import stream_run

__all__ = ["router", "run_service"]

router = APIRouter(tags=["runs"])

Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]
RunId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
TaskId = Annotated[str, Path(pattern=TASK_ID_PATTERN)]
OptionalReason = Annotated[RunControlRequest | None, Body()]

DEFAULT_RUN_LIMIT = 20
MAX_RUN_LIMIT = 100
MAX_RUN_PAGE = 1000


def run_service(request: Request) -> RunService:
    service: RunService | None = getattr(request.app.state, "runs", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Runs = Annotated[RunService, Depends(run_service)]


def _control(runs: RunService) -> RunControl:
    if runs.control is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return runs.control


def _reason(body: RunControlRequest | None) -> str | None:
    return None if body is None else body.reason


@router.post("/runs", response_model=RunEnvelope, status_code=201, operation_id="startRun")
async def start_run(principal: Hr, runs: Runs, body: RunCreate) -> Envelope[RunRecord]:
    return respond(await runs.start(principal, body))


@router.get("/runs", response_model=RunsEnvelope, operation_id="listRuns")
async def list_runs(
    principal: CurrentPrincipal,
    runs: Runs,
    status: RunStatus | None = None,
    page: Annotated[int, Query(ge=1, le=MAX_RUN_PAGE)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_RUN_LIMIT)] = DEFAULT_RUN_LIMIT,
) -> Envelope[list[RunSummary]]:
    found, total = await _control(runs).list_runs(
        principal, status=None if status is None else RunState(status), page=page, limit=limit
    )
    return respond(found, page=PageMeta(total=total, page=page, limit=limit))


@router.get("/runs/{run_id}", response_model=RunEnvelope, operation_id="getRun")
async def get_run(principal: CurrentPrincipal, runs: Runs, run_id: RunId) -> Envelope[RunRecord]:
    return respond(await _control(runs).get(principal, run_id))


@router.post("/runs/{run_id}/pause", response_model=RunEnvelope, operation_id="pauseRun")
async def pause_run(principal: Hr, runs: Runs, run_id: RunId, body: OptionalReason = None) -> Envelope[RunRecord]:
    return respond(await _control(runs).pause(principal, run_id, _reason(body)))


@router.post("/runs/{run_id}/resume", response_model=RunEnvelope, operation_id="resumeRun")
async def resume_run(principal: Hr, runs: Runs, run_id: RunId, body: OptionalReason = None) -> Envelope[RunRecord]:
    return respond(await _control(runs).resume(principal, run_id, _reason(body)))


@router.post("/runs/{run_id}/cancel", response_model=RunEnvelope, operation_id="cancelRun")
async def cancel_run(principal: Hr, runs: Runs, run_id: RunId, body: OptionalReason = None) -> Envelope[RunRecord]:
    return respond(await _control(runs).cancel(principal, run_id, _reason(body)))


@router.post("/runs/{run_id}/tasks/{task_id}/retry", response_model=RunEnvelope, operation_id="retryRunTask")
async def retry_task(
    principal: Hr, runs: Runs, run_id: RunId, task_id: TaskId, body: OptionalReason = None
) -> Envelope[RunRecord]:
    return respond(await _control(runs).retry_task(principal, run_id, task_id, _reason(body)))


@router.post("/runs/{run_id}/tasks/{task_id}/complete", response_model=RunEnvelope, operation_id="completeRunTask")
async def complete_task(
    principal: Hr, runs: Runs, run_id: RunId, task_id: TaskId, body: TaskCompleteRequest
) -> Envelope[RunRecord]:
    return respond(await _control(runs).complete_task(principal, run_id, task_id, body.reason))


@router.get(
    "/runs/{run_id}/stream",
    operation_id="streamRun",
    response_class=StreamingResponse,
    responses={200: {"description": "Server-sent events: task, run and end.", "content": {"text/event-stream": {}}}},
)
async def stream(principal: CurrentPrincipal, runs: Runs, run_id: RunId) -> StreamingResponse:
    control = _control(runs)
    await control.visible_run(principal, run_id)  # 404 before the stream starts

    def events() -> AsyncIterator[str]:
        return stream_run(
            control.store,
            principal.org_id,
            run_id,
            poll_seconds=runs.stream_poll_seconds,
            max_seconds=runs.stream_max_seconds,
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
