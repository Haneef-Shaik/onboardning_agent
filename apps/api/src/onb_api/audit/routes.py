"""`GET /audit`: the org's audit log for HR_ADMIN, filtered and paged (PRD §7.6, §10; AUD-1).

HR and MANAGER get 403 before the log is touched. The org is the token's (the route runs inside its
tenant scope); no parameter names an org. Filters match exactly; `since`/`until` must carry a time
zone. Records come newest first, and `meta.page` carries the total. Paging stops at
`MAX_AUDIT_SKIP` records deep (422 beyond), so a huge page cannot overflow the skip or scan the log.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from onb_contracts import Envelope, ErrorCode, ErrorDetail, PageMeta
from onb_contracts.audit import (
    AUDIT_ACTION_PATTERN,
    AUDIT_ACTOR_ID_PATTERN,
    AUDIT_TARGET_PATTERN,
    DEFAULT_AUDIT_LIMIT,
    MAX_AUDIT_ACTION_LENGTH,
    MAX_AUDIT_LIMIT,
    MAX_AUDIT_SKIP,
    MAX_AUDIT_TARGET_LENGTH,
    AuditActor,
    AuditActorType,
    AuditOutcome,
    AuditRecord,
    AuditRecordsEnvelope,
)
from onb_contracts.auth import Role
from onb_data import ActorType, AuditEntry, AuditQuery
from onb_data import AuditOutcome as DataOutcome
from pydantic import AwareDatetime

from onb_api.audit.reader import AuditReader
from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond

__all__ = ["audit_reader", "router"]

router = APIRouter(tags=["audit"])

HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]
TargetQuery = Query(min_length=1, max_length=MAX_AUDIT_TARGET_LENGTH, pattern=AUDIT_TARGET_PATTERN)


def audit_reader(request: Request) -> AuditReader:
    log: AuditReader | None = request.app.state.audit
    if log is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return log


def _record(entry: AuditEntry) -> AuditRecord:
    return AuditRecord(
        id=entry.id,
        org_id=entry.org_id,
        actor=AuditActor(type=AuditActorType(entry.actor.type.value), id=entry.actor.id),
        action=entry.action,
        target=entry.target,
        outcome=AuditOutcome(entry.outcome.value),
        reason=entry.reason,
        trace_id=entry.trace_id,
        at=entry.at,
    )


@router.get("/audit", response_model=AuditRecordsEnvelope, operation_id="listAudit")
async def list_audit(
    _: HrAdmin,
    log: Annotated[AuditReader, Depends(audit_reader)],
    actor_type: AuditActorType | None = None,
    actor_id: Annotated[str | None, Query(pattern=AUDIT_ACTOR_ID_PATTERN)] = None,
    action: Annotated[str | None, Query(max_length=MAX_AUDIT_ACTION_LENGTH, pattern=AUDIT_ACTION_PATTERN)] = None,
    outcome: AuditOutcome | None = None,
    target: Annotated[str | None, TargetQuery] = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    page: Annotated[int, Query(ge=1, le=MAX_AUDIT_SKIP + 1)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_LIMIT)] = DEFAULT_AUDIT_LIMIT,
) -> Envelope[list[AuditRecord]]:
    if since is not None and until is not None and since > until:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="query.since", issue="must not be after until"),),
        )
    if (page - 1) * limit > MAX_AUDIT_SKIP:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="query.page", issue=f"pages reach at most {MAX_AUDIT_SKIP} records deep"),),
        )
    query = AuditQuery(
        actor_type=None if actor_type is None else ActorType(actor_type.value),
        actor_id=actor_id,
        action=action,
        outcome=None if outcome is None else DataOutcome(outcome.value),
        target=target,
        since=since,
        until=until,
    )
    found = await log.list(query, page=page, limit=limit)
    records = [_record(entry) for entry in found.entries]
    return respond(records, page=PageMeta(total=found.total, page=found.page, limit=found.limit))
