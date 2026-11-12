"""Approval routes (PRD FR-H2, FR-H3, §10 Approvals; HIL-1).

| Route | Who |
|---|---|
| `GET /approvals?mine=true` | everyone: the approvals they may decide; `mine=false` (HR, HR_ADMIN) the org's |
| `POST /approvals/{id}/decision` | the approval's approver; first decision wins, a later one is 409 |

The org is the token's. An approval of another org, or one the caller is not the approver of, is 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from onb_contracts import Envelope, ErrorCode, PageMeta
from onb_contracts.approvals import (
    APPROVAL_ID_PATTERN,
    ApprovalDecisionEnvelope,
    ApprovalDecisionRequest,
    ApprovalListEnvelope,
    ApprovalRecord,
    ApprovalStatus,
)
from onb_contracts.auth import Role

from onb_api.approvals.service import ApprovalService
from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond

__all__ = ["approval_service", "router"]

router = APIRouter(tags=["approvals"])

AnyRole = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR, Role.MANAGER))]
MAX_PAGE = 100
MAX_LIMIT = 100


def approval_service(request: Request) -> ApprovalService:
    service: ApprovalService | None = getattr(request.app.state, "approvals", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Approvals = Annotated[ApprovalService, Depends(approval_service)]


@router.get("/approvals", response_model=ApprovalListEnvelope, operation_id="listApprovals")
async def list_approvals(
    principal: AnyRole,
    approvals: Approvals,
    mine: bool = True,
    status: Annotated[ApprovalStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> Envelope[list[ApprovalRecord]]:
    found = await approvals.list(principal, mine=mine, status=status, page=page, limit=limit)
    return respond(list(found.records), page=PageMeta(total=found.total, page=page, limit=limit))


@router.post(
    "/approvals/{approval_id}/decision", response_model=ApprovalDecisionEnvelope, operation_id="decideApproval"
)
async def decide_approval(
    principal: AnyRole,
    approvals: Approvals,
    approval_id: Annotated[str, Path(pattern=APPROVAL_ID_PATTERN, max_length=70)],
    body: ApprovalDecisionRequest,
) -> Envelope[ApprovalRecord]:
    return respond(await approvals.decide(principal, approval_id, body))
