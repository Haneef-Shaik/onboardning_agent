"""`POST /runs` (PRD FR-R1, §10 Hires & runs; RUN-1).

| Route | Roles |
|---|---|
| `POST /runs` | HR, HR_ADMIN |

The org is the token's. The hire and the template version are read in that org only, so another
org's hire or version is 404, exactly like one that does not exist. The started run (201) is pinned to
the requested version: its key, version and DAG hash never change. Reading and controlling runs
belong to RUN-2.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from onb_contracts import Envelope, ErrorCode
from onb_contracts.auth import Role
from onb_contracts.runs import RunCreate, RunEnvelope, RunRecord

from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond
from onb_api.runs.service import RunService

__all__ = ["router", "run_service"]

router = APIRouter(tags=["runs"])

Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]


def run_service(request: Request) -> RunService:
    service: RunService | None = getattr(request.app.state, "runs", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Runs = Annotated[RunService, Depends(run_service)]


@router.post("/runs", response_model=RunEnvelope, status_code=201, operation_id="startRun")
async def start_run(principal: Hr, runs: Runs, body: RunCreate) -> Envelope[RunRecord]:
    return respond(await runs.start(principal, body))
