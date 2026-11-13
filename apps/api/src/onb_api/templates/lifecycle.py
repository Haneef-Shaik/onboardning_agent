"""The template lifecycle: `draft → published → archived` (PRD FR-T3, FR-T4; TPL-2).

Every call runs in the caller's tenant scope (the route's token), so the store sees one org only.

- Saving a draft validates it (FR-T3, "validation on save") and stores it with the issues found.
  A draft with a `sensitive_input` issue is refused and not stored: raw PII never lands in a draft.
- Publishing validates again against the registry as it is now; an invalid draft raises
  `TemplateRejectedError` and the attempt is audited as failed. A valid draft becomes the key's next
  version in one transaction: the head's version counter, the immutable version, the draft's lock and
  the audit record all commit together or not at all (AUD-1: an action whose audit write failed is
  not done). Publishing a draft that is already published returns its version and changes nothing.
- Archiving marks one version archived on its head, audited in the same transaction. Archiving an
  archived version returns it unchanged.

The callers own the role checks; this class owns the rules of the lifecycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Final

from onb_agents.templates import Issue, Rule, TemplateValidator
from onb_contracts.template_lifecycle import (
    DraftRecord,
    TemplateIssue,
    TemplateSummary,
    TemplateValidation,
    TemplateVersionRecord,
    TemplateVersionSummary,
)
from onb_contracts.templates import Template
from onb_data import Actor, Auditor, AuditOutcome, NotFoundError

from onb_api.templates.store import StoredDraft, StoredVersion, TemplateHead, TemplateStore, VersionEntry
from onb_api.templates.validators import ValidatorSource

__all__ = [
    "ARCHIVE_ACTION",
    "PUBLISH_ACTION",
    "DraftLockedError",
    "DraftRefusedError",
    "LifecycleConflictError",
    "TemplateLifecycle",
    "TemplateRejectedError",
    "draft_target",
    "version_target",
]

PUBLISH_ACTION: Final = "template.publish"
ARCHIVE_ACTION: Final = "template.archive"
_LIFECYCLE_FIELDS = frozenset({"version", "status"})


class LifecycleConflictError(Exception):
    """The record moved on under the caller (a stale revision, a concurrent publish)."""


class DraftLockedError(LifecycleConflictError):
    """The draft was published: its version is fixed, so the draft no longer changes."""


class TemplateRejectedError(Exception):
    """FR-T3 issues that stop a publish; `issues` are the validator's, without raw values."""

    def __init__(self, issues: tuple[Issue, ...]) -> None:
        super().__init__(f"{len(issues)} issue(s)")
        self.issues = issues


class DraftRefusedError(TemplateRejectedError):
    """A draft that may not be stored at all (it references or holds sensitive data)."""


def draft_target(draft_id: str) -> str:
    return f"template_draft:{draft_id}"


def version_target(key: str, version: int) -> str:
    return f"template:{key}/v{version}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_draft_id() -> str:
    return f"tdr_{uuid.uuid4().hex}"


def _body(template: Template) -> dict[str, Any]:
    """The stored body: the template without its lifecycle-owned fields."""
    return template.model_dump(mode="json", exclude=set(_LIFECYCLE_FIELDS))


def _issue(issue: Issue) -> TemplateIssue:
    return TemplateIssue(rule=issue.rule.value, task_id=issue.task_id, message=issue.message)


class TemplateLifecycle:
    def __init__(
        self,
        store: TemplateStore,
        validators: ValidatorSource,
        auditor: Auditor,
        *,
        clock: Callable[[], datetime] = _utc_now,
        new_draft_id: Callable[[], str] = _new_draft_id,
    ) -> None:
        self._store = store
        self._validators = validators
        self._auditor = auditor
        self._clock = clock
        self._new_draft_id = new_draft_id

    @property
    def auditor(self) -> Auditor:
        """Where lifecycle actions are audited; routes record refused attempts here too."""
        return self._auditor

    async def ensure_indexes(self) -> None:
        await self._store.ensure_indexes()

    # --- drafts -------------------------------------------------------------------------------------

    async def create_draft(self, template: Template, *, actor_id: str) -> DraftRecord:
        body, issues = await self._checked(template)
        now = self._clock()
        draft = StoredDraft(
            id=self._new_draft_id(),
            key=template.key,
            template=body,
            issues=tuple(issue.document() for issue in issues),
            revision=1,
            status="draft",
            published_version=None,
            created_by=actor_id,
            created_at=now,
            updated_by=actor_id,
            updated_at=now,
        )
        await self._store.insert_draft(draft)
        return _draft_record(draft)

    async def get_draft(self, draft_id: str) -> DraftRecord:
        return _draft_record(await self._draft(draft_id))

    async def list_drafts(self) -> list[DraftRecord]:
        """Every draft of the org (the seeder's lookup); the route pages with `page_drafts`."""
        return [_draft_record(draft) for draft in await self._store.list_drafts()]

    async def page_drafts(self, *, page: int, limit: int) -> tuple[list[DraftRecord], int]:
        """One page of the org's drafts, most recently updated first, and the org's total."""
        drafts, total = await self._store.page_drafts(page=page, limit=limit)
        return [_draft_record(draft) for draft in drafts], total

    async def save_draft(
        self, draft_id: str, template: Template, *, revision: int | None, actor_id: str
    ) -> DraftRecord:
        current = await self._draft(draft_id)
        if current.status != "draft":
            raise DraftLockedError("the draft was published")
        if revision is not None and revision != current.revision:
            raise LifecycleConflictError("the draft has changed since that revision")
        body, issues = await self._checked(template)
        saved = StoredDraft(
            id=current.id,
            key=template.key,
            template=body,
            issues=tuple(issue.document() for issue in issues),
            revision=current.revision + 1,
            status="draft",
            published_version=None,
            created_by=current.created_by,
            created_at=current.created_at,
            updated_by=actor_id,
            updated_at=self._clock(),
        )
        if not await self._store.replace_draft(saved, expected_revision=current.revision):
            raise LifecycleConflictError("the draft changed while saving")
        return _draft_record(saved)

    async def validate_draft(self, draft_id: str) -> TemplateValidation:
        draft = await self._draft(draft_id)
        report = (await self._validators.current()).validate(dict(draft.template))
        return TemplateValidation(ok=report.ok, issues=[_issue(i) for i in report.issues], order=list(report.order))

    # --- publish ------------------------------------------------------------------------------------

    async def publish(self, draft_id: str, *, actor_id: str) -> TemplateVersionRecord:
        draft = await self._draft(draft_id)
        if draft.status == "published" and draft.published_version is not None:
            return await self.get_version(draft.key, draft.published_version)
        validator = await self._validators.current()
        report = validator.validate(dict(draft.template))
        if not report.ok or report.template is None:
            await self._auditor.audit(
                actor=Actor.user(actor_id),
                action=PUBLISH_ACTION,
                target=draft_target(draft.id),
                outcome=AuditOutcome.FAILED,
                reason=f"validation failed: {len(report.issues)} issue(s)",
            )
            raise TemplateRejectedError(report.issues)
        template = report.template

        async def apply() -> StoredVersion:
            return await self._append_version(draft, template, actor_id)

        stored = await self._store.atomically(apply)
        return await self.get_version(stored.key, stored.version)

    async def _append_version(self, draft: StoredDraft, template: Template, actor_id: str) -> StoredVersion:
        now = self._clock()
        head = await self._store.get_template(template.key)
        number = 1 if head is None else head.latest_version + 1
        entry = VersionEntry(version=number, published_at=now, published_by=actor_id)
        if head is None:
            fresh = TemplateHead(id=uuid.uuid4().hex, key=template.key, name=None, latest_version=0)
            advanced = await self._store.insert_template(fresh.with_version(entry, template.name))
        else:
            advanced = await self._store.append_version_entry(
                template.key, expected_latest=head.latest_version, entry=entry, name=template.name
            )
        if not advanced:
            raise LifecycleConflictError("another version of this template was published at the same time")
        stored = StoredVersion(
            id=uuid.uuid4().hex,
            key=template.key,
            version=number,
            template=_body(template),
            draft_id=draft.id,
            published_at=now,
            published_by=actor_id,
        )
        await self._store.insert_version(stored)
        locked = replace(
            draft,
            revision=draft.revision + 1,
            status="published",
            published_version=number,
            updated_by=actor_id,
            updated_at=now,
        )
        if not await self._store.replace_draft(locked, expected_revision=draft.revision):
            raise LifecycleConflictError("the draft changed while publishing")
        await self._auditor.audit(
            actor=Actor.user(actor_id),
            action=PUBLISH_ACTION,
            target=version_target(template.key, number),
            outcome=AuditOutcome.SUCCESS,
            reason=f"published from {draft_target(draft.id)}",
        )
        return stored

    # --- versions -----------------------------------------------------------------------------------

    async def list_templates(self) -> list[TemplateSummary]:
        return [
            TemplateSummary(
                key=head.key,
                name=head.name,
                latest_version=head.latest_version,
                versions=[_version_summary(entry) for entry in head.versions],
            )
            for head in await self._store.list_templates()
        ]

    async def get_version(self, key: str, version: int) -> TemplateVersionRecord:
        head = await self._store.get_template(key)
        entry = None if head is None else head.entry(version)
        stored = None if entry is None else await self._store.get_version(key, version)
        if entry is None or stored is None:
            raise NotFoundError("no such template version")
        return _version_record(stored, entry)

    async def archive(self, key: str, version: int, *, actor_id: str) -> TemplateVersionRecord:
        current = await self.get_version(key, version)
        if current.status == "archived":
            return current

        async def apply() -> bool:
            if not await self._store.archive_version(key, version, at=self._clock(), by=actor_id):
                return False
            await self._auditor.audit(
                actor=Actor.user(actor_id),
                action=ARCHIVE_ACTION,
                target=version_target(key, version),
                outcome=AuditOutcome.SUCCESS,
            )
            return True

        await self._store.atomically(apply)  # False: archived concurrently, which is the same end state
        return await self.get_version(key, version)

    # --- helpers ------------------------------------------------------------------------------------

    async def _draft(self, draft_id: str) -> StoredDraft:
        draft = await self._store.get_draft(draft_id)
        if draft is None:
            raise NotFoundError("no such draft")
        return draft

    async def _checked(self, template: Template) -> tuple[dict[str, Any], tuple[Issue, ...]]:
        """The body to store and its issues; `DraftRefusedError` when it may not be stored."""
        validator: TemplateValidator = await self._validators.current()
        report = validator.validate(template)
        sensitive = tuple(issue for issue in report.issues if issue.rule is Rule.SENSITIVE_INPUT)
        if sensitive:
            raise DraftRefusedError(sensitive)
        return _body(template), report.issues


def _draft_record(draft: StoredDraft) -> DraftRecord:
    template = Template.model_validate({**draft.template, "status": "draft"})
    return DraftRecord(
        id=draft.id,
        key=draft.key,
        revision=draft.revision,
        status="published" if draft.status == "published" else "draft",
        template=template,
        issues=[TemplateIssue.model_validate(issue) for issue in draft.issues],
        published_version=draft.published_version,
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_by=draft.updated_by,
        updated_at=draft.updated_at,
    )


def _status(entry: VersionEntry) -> str:
    return "archived" if entry.archived else "published"


def _version_summary(entry: VersionEntry) -> TemplateVersionSummary:
    return TemplateVersionSummary.model_validate(
        {
            "version": entry.version,
            "status": _status(entry),
            "published_at": entry.published_at,
            "published_by": entry.published_by,
            "archived_at": entry.archived_at,
            "archived_by": entry.archived_by,
        }
    )


def _version_record(stored: StoredVersion, entry: VersionEntry) -> TemplateVersionRecord:
    status = _status(entry)
    template = Template.model_validate({**stored.template, "version": stored.version, "status": status})
    return TemplateVersionRecord.model_validate(
        {
            **_version_summary(entry).model_dump(),
            "key": stored.key,
            "template": template,
            "draft_id": stored.draft_id,
        }
    )
