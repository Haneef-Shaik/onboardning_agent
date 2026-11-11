"""`/templates`: drafts, validation, publish, versions and archive (PRD FR-T3, FR-T4, U2, §10 Templates).

Roles (PRD §3): HR and HR_ADMIN author drafts; only HR_ADMIN publishes and archives, and a refused
publish or archive is audited as denied; everyone (managers included, for suggest mode) reads the
published templates. The org is the token's: every call runs in its tenant scope, so another org's
draft or version is 404, exactly like one that does not exist. No route writes to a version.

Errors: an invalid draft on publish is 422 with one detail per validator issue (`field`: the task or
`template`; `issue`: `<rule>: <message>`, never a submitted value). A draft that holds sensitive data
is refused the same way on save. A published draft, or a stale `revision`, is 409.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from onb_contracts import Envelope, ErrorCode, ErrorDetail
from onb_contracts.auth import Role
from onb_contracts.template_lifecycle import (
    DRAFT_ID_PATTERN,
    MAX_TEMPLATE_VERSION,
    TEMPLATE_KEY_PATTERN,
    DraftCreate,
    DraftEnvelope,
    DraftRecord,
    DraftsEnvelope,
    DraftUpdate,
    TemplateSummariesEnvelope,
    TemplateSummary,
    TemplateValidation,
    TemplateValidationEnvelope,
    TemplateVersionEnvelope,
    TemplateVersionRecord,
)
from onb_data import Actor, AuditOutcome

from onb_api.auth.dependencies import CurrentPrincipal, require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond
from onb_api.templates.lifecycle import (
    ARCHIVE_ACTION,
    PUBLISH_ACTION,
    LifecycleConflictError,
    TemplateLifecycle,
    TemplateRejectedError,
    draft_target,
    version_target,
)
from onb_api.templates.store import VersionExistsError

__all__ = ["router", "template_lifecycle"]

router = APIRouter(tags=["templates"])

Author = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR))]
DraftId = Annotated[str, Path(max_length=36, pattern=DRAFT_ID_PATTERN)]
TemplateKey = Annotated[str, Path(max_length=64, pattern=TEMPLATE_KEY_PATTERN)]
VersionNumber = Annotated[int, Path(ge=1, le=MAX_TEMPLATE_VERSION)]

_DRAFT_ID = re.compile(DRAFT_ID_PATTERN)
_KEY = re.compile(TEMPLATE_KEY_PATTERN)
_VERSION = re.compile(r"^[1-9][0-9]{0,6}$")
_INVALID_DRAFT = "template_draft:invalid"
_INVALID_VERSION = "template:invalid"


def template_lifecycle(request: Request) -> TemplateLifecycle:
    lifecycle: TemplateLifecycle | None = request.app.state.templates
    if lifecycle is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return lifecycle


Lifecycle = Annotated[TemplateLifecycle, Depends(template_lifecycle)]


# --- HR_ADMIN only, with refusals audited ----------------------------------------------------------------


def _draft_target(params: Mapping[str, Any]) -> str:
    draft_id = str(params.get("draft_id", ""))
    return draft_target(draft_id) if _DRAFT_ID.fullmatch(draft_id) else _INVALID_DRAFT


def _version_target(params: Mapping[str, Any]) -> str:
    key, version = str(params.get("key", "")), str(params.get("version", ""))
    if _KEY.fullmatch(key) and _VERSION.fullmatch(version) and int(version) <= MAX_TEMPLATE_VERSION:
        return version_target(key, int(version))
    return _INVALID_VERSION


def _admin_audited(
    action: str, target_of: Callable[[Mapping[str, Any]], str]
) -> Callable[[Request, Principal], Awaitable[Principal]]:
    """HR_ADMIN passes; anyone else gets 403, and the refusal is audited first (fails closed: no 403
    is reported without its record)."""

    async def check(request: Request, principal: CurrentPrincipal) -> Principal:
        if principal.role is Role.HR_ADMIN:
            return principal
        lifecycle = template_lifecycle(request)
        await lifecycle.auditor.audit(
            actor=Actor.user(principal.user_id),
            action=action,
            target=target_of(request.path_params),
            outcome=AuditOutcome.DENIED,
            reason=f"role {principal.role.value} may not do this",
        )
        raise AppError(ErrorCode.FORBIDDEN)

    return check


Publisher = Annotated[Principal, Depends(_admin_audited(PUBLISH_ACTION, _draft_target))]
Archiver = Annotated[Principal, Depends(_admin_audited(ARCHIVE_ACTION, _version_target))]


def _invalid(error: TemplateRejectedError) -> AppError:
    details = [
        ErrorDetail(
            field=f"tasks.{issue.task_id}" if issue.task_id else "template",
            issue=f"{issue.rule.value}: {issue.message}",
        )
        for issue in error.issues
    ]
    return AppError(ErrorCode.VALIDATION_ERROR, "The template is not valid", details=details)


async def _guarded[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except TemplateRejectedError as error:
        raise _invalid(error) from None
    except LifecycleConflictError as error:
        raise AppError(ErrorCode.CONFLICT, str(error)) from None
    except VersionExistsError:
        raise AppError(ErrorCode.CONFLICT, "that version is already published") from None


# --- drafts --------------------------------------------------------------------------------------------


@router.post("/templates/drafts", response_model=DraftEnvelope, status_code=201, operation_id="createTemplateDraft")
async def create_draft(principal: Author, lifecycle: Lifecycle, body: DraftCreate) -> Envelope[DraftRecord]:
    return respond(await _guarded(lifecycle.create_draft(body.template, actor_id=principal.user_id)))


@router.get("/templates/drafts", response_model=DraftsEnvelope, operation_id="listTemplateDrafts")
async def list_drafts(_: Author, lifecycle: Lifecycle) -> Envelope[list[DraftRecord]]:
    return respond(await lifecycle.list_drafts())


@router.get("/templates/drafts/{draft_id}", response_model=DraftEnvelope, operation_id="getTemplateDraft")
async def get_draft(_: Author, lifecycle: Lifecycle, draft_id: DraftId) -> Envelope[DraftRecord]:
    return respond(await lifecycle.get_draft(draft_id))


@router.put("/templates/drafts/{draft_id}", response_model=DraftEnvelope, operation_id="saveTemplateDraft")
async def save_draft(
    principal: Author, lifecycle: Lifecycle, draft_id: DraftId, body: DraftUpdate
) -> Envelope[DraftRecord]:
    saved = lifecycle.save_draft(draft_id, body.template, revision=body.revision, actor_id=principal.user_id)
    return respond(await _guarded(saved))


@router.post(
    "/templates/drafts/{draft_id}/validate",
    response_model=TemplateValidationEnvelope,
    operation_id="validateTemplateDraft",
)
async def validate_draft(_: Author, lifecycle: Lifecycle, draft_id: DraftId) -> Envelope[TemplateValidation]:
    return respond(await lifecycle.validate_draft(draft_id))


@router.post(
    "/templates/drafts/{draft_id}/publish",
    response_model=TemplateVersionEnvelope,
    operation_id="publishTemplateDraft",
)
async def publish_draft(
    principal: Publisher, lifecycle: Lifecycle, draft_id: DraftId
) -> Envelope[TemplateVersionRecord]:
    return respond(await _guarded(lifecycle.publish(draft_id, actor_id=principal.user_id)))


# --- published templates ---------------------------------------------------------------------------------


@router.get("/templates", response_model=TemplateSummariesEnvelope, operation_id="listTemplates")
async def list_templates(_: CurrentPrincipal, lifecycle: Lifecycle) -> Envelope[list[TemplateSummary]]:
    return respond(await lifecycle.list_templates())


@router.get(
    "/templates/{key}/versions/{version}",
    response_model=TemplateVersionEnvelope,
    operation_id="getTemplateVersion",
)
async def get_version(
    _: CurrentPrincipal, lifecycle: Lifecycle, key: TemplateKey, version: VersionNumber
) -> Envelope[TemplateVersionRecord]:
    return respond(await lifecycle.get_version(key, version))


@router.post(
    "/templates/{key}/versions/{version}/archive",
    response_model=TemplateVersionEnvelope,
    operation_id="archiveTemplateVersion",
)
async def archive_version(
    principal: Archiver, lifecycle: Lifecycle, key: TemplateKey, version: VersionNumber
) -> Envelope[TemplateVersionRecord]:
    return respond(await _guarded(lifecycle.archive(key, version, actor_id=principal.user_id)))
