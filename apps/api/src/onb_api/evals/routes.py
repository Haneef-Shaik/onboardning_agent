"""`GET /evals/runs`, `GET /evals/runs/{run_id}`: the org's eval runs for HR_ADMIN (PRD §9, §10 Evals; UI-8).

Read-only. HR and MANAGER get 403 before the store is read. The org is the token's (the route runs
inside its tenant scope); no parameter names an org, and another org's run is 404 exactly like a run
that does not exist. Runs come newest first; paging stops at `MAX_EVAL_RUN_SKIP` runs deep (422).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from onb_contracts import Envelope, ErrorCode, ErrorDetail, PageMeta
from onb_contracts.auth import Role
from onb_contracts.evals import (
    DEFAULT_EVAL_RUN_LIMIT,
    EVAL_AGENT_PATTERN,
    EVAL_RUN_ID_PATTERN,
    MAX_EVAL_RUN_LIMIT,
    MAX_EVAL_RUN_SKIP,
    EvalRun,
    EvalRunEnvelope,
    EvalRunsEnvelope,
    EvalRunSummary,
)

from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.evals.store import EvalRunQuery, EvalRunsReader
from onb_api.responses import respond

__all__ = ["eval_runs", "router"]

router = APIRouter(tags=["evals"])

HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]


def eval_runs(request: Request) -> EvalRunsReader:
    reader: EvalRunsReader | None = request.app.state.evals
    if reader is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return reader


Runs = Annotated[EvalRunsReader, Depends(eval_runs)]


@router.get("/evals/runs", response_model=EvalRunsEnvelope, operation_id="listEvalRuns")
async def list_eval_runs(
    _: HrAdmin,
    runs: Runs,
    agent: Annotated[str | None, Query(max_length=64, pattern=EVAL_AGENT_PATTERN)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_EVAL_RUN_SKIP + 1)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_EVAL_RUN_LIMIT)] = DEFAULT_EVAL_RUN_LIMIT,
) -> Envelope[list[EvalRunSummary]]:
    if (page - 1) * limit > MAX_EVAL_RUN_SKIP:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="query.page", issue=f"pages reach at most {MAX_EVAL_RUN_SKIP} runs deep"),),
        )
    found, total = await runs.page(EvalRunQuery(agent=agent), page=page, limit=limit)
    return respond(found, page=PageMeta(total=total, page=page, limit=limit))


@router.get("/evals/runs/{run_id}", response_model=EvalRunEnvelope, operation_id="getEvalRun")
async def get_eval_run(
    _: HrAdmin, runs: Runs, run_id: Annotated[str, Path(max_length=64, pattern=EVAL_RUN_ID_PATTERN)]
) -> Envelope[EvalRun]:
    run = await runs.get(run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return respond(run)
