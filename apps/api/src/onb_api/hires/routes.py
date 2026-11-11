"""Hire and PII routes (PRD §10 Hires & runs, Registry & audit; HIRE-1).

| Route | Roles |
|---|---|
| `POST /hires` | HR, HR_ADMIN |
| `GET /hires`, `GET /hires/{hire_id}` | all (MANAGER: own reports only), always masked |
| `PUT /hires/{hire_id}/pii/{field}` | HR, HR_ADMIN |
| `POST /hires/{hire_id}/pii/{field}/reveal` | HR, HR_ADMIN; Aadhaar HR_ADMIN only; reason required |
| `GET /pii-access-log` | HR_ADMIN |

A role outside the list is 403 before any data is read. Another org's hire, or (for a MANAGER)
another manager's report, is 404 like a hire that does not exist. Bodies are never logged; a reveal's
answer is marked `Cache-Control: no-store`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from onb_contracts import Envelope, ErrorCode, PageMeta
from onb_contracts.auth import Role
from onb_contracts.hires import (
    DEFAULT_ACCESS_LOG_LIMIT,
    DEFAULT_HIRE_LIMIT,
    HIRE_ID_PATTERN,
    MAX_ACCESS_LOG_LIMIT,
    MAX_HIRE_LIMIT,
    MAX_HIRE_SKIP,
    HireEnvelope,
    HireRecord,
    HiresEnvelope,
    NewHireRequest,
    PiiAccessEnvelope,
    PiiAccessKind,
    PiiAccessRecord,
    PiiField,
    PiiUpdateRequest,
    RevealedValue,
    RevealEnvelope,
    RevealRequest,
)

from onb_api.auth.dependencies import CurrentPrincipal, require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.hires.access_log import AccessQuery
from onb_api.hires.service import HireService
from onb_api.responses import respond

__all__ = ["hire_service", "router"]

router = APIRouter(tags=["hires"])

Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]
HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]
HireId = Annotated[str, Path(max_length=66, pattern=HIRE_ID_PATTERN)]
Page = Annotated[int, Query(ge=1, le=MAX_HIRE_SKIP + 1)]


def hire_service(request: Request) -> HireService:
    service: HireService | None = request.app.state.hires
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Hires = Annotated[HireService, Depends(hire_service)]


def _check_depth(page: int, limit: int) -> None:
    if (page - 1) * limit > MAX_HIRE_SKIP:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Pages reach at most {MAX_HIRE_SKIP} records deep")


@router.post("/hires", response_model=HireEnvelope, status_code=201, operation_id="createHire")
async def create_hire(principal: Hr, hires: Hires, body: NewHireRequest) -> Envelope[HireRecord]:
    return respond(await hires.create(principal, body))


@router.get("/hires", response_model=HiresEnvelope, operation_id="listHires")
async def list_hires(
    principal: CurrentPrincipal,
    hires: Hires,
    page: Page = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_HIRE_LIMIT)] = DEFAULT_HIRE_LIMIT,
) -> Envelope[list[HireRecord]]:
    _check_depth(page, limit)
    records, total = await hires.list_hires(principal, page=page, limit=limit)
    return respond(records, page=PageMeta(total=total, page=page, limit=limit))


@router.get("/hires/{hire_id}", response_model=HireEnvelope, operation_id="getHire")
async def get_hire(principal: CurrentPrincipal, hires: Hires, hire_id: HireId) -> Envelope[HireRecord]:
    return respond(await hires.get(principal, hire_id))


@router.put("/hires/{hire_id}/pii/{field}", response_model=HireEnvelope, operation_id="updateHirePii")
async def update_hire_pii(
    principal: Hr, hires: Hires, hire_id: HireId, field: PiiField, body: PiiUpdateRequest
) -> Envelope[HireRecord]:
    return respond(await hires.update(principal, hire_id, field, body))


@router.post("/hires/{hire_id}/pii/{field}/reveal", response_model=RevealEnvelope, operation_id="revealHirePii")
async def reveal_hire_pii(
    principal: Hr, hires: Hires, hire_id: HireId, field: PiiField, body: RevealRequest, response: Response
) -> Envelope[RevealedValue]:
    revealed = await hires.reveal(principal, hire_id, field, body.reason)
    response.headers["Cache-Control"] = "no-store"
    return respond(revealed)


@router.get("/pii-access-log", response_model=PiiAccessEnvelope, operation_id="listPiiAccessLog")
async def list_pii_access_log(
    _: HrAdmin,
    hires: Hires,
    hire_id: Annotated[str | None, Query(max_length=66, pattern=HIRE_ID_PATTERN)] = None,
    field: PiiField | None = None,
    kind: PiiAccessKind | None = None,
    page: Page = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_ACCESS_LOG_LIMIT)] = DEFAULT_ACCESS_LOG_LIMIT,
) -> Envelope[list[PiiAccessRecord]]:
    _check_depth(page, limit)
    records, total = await hires.access_log(AccessQuery(hire_id, field, kind), page=page, limit=limit)
    return respond(records, page=PageMeta(total=total, page=page, limit=limit))
