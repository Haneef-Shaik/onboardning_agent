"""Where templates live (PRD §9): `template_drafts`, `templates` and `template_versions`.

- `template_drafts`: one document per draft, edited in place under a revision check.
- `templates`: one head per key in the org (unique `(org_id, key)`): the latest version number and a
  summary of every version, including whether it is archived. Version numbers are handed out by a
  compare-and-set on `latest_version`, so two publishes can never take the same number.
- `template_versions`: the published bodies, insert-only. The store has no operation that updates or
  deletes a version, and a unique `(org_id, key, version)` index refuses a second document for a
  version that exists, so a version cannot be overwritten either. (Agents read this collection
  through mcp-mongo and may not write it: `AGENT_WRITABLE_COLLECTIONS`.)

Everything goes through `onb_data` repositories in the caller's tenant scope. `MongoTemplateStore`
is the production store; `onb_api.templates.memory.InMemoryTemplateStore` keeps the same contract
for tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from onb_data import DataStore
from pymongo.errors import DuplicateKeyError

__all__ = [
    "DRAFTS",
    "TEMPLATES",
    "VERSIONS",
    "MongoTemplateStore",
    "StoredDraft",
    "StoredVersion",
    "TemplateHead",
    "TemplateStore",
    "VersionEntry",
    "VersionExistsError",
]

DRAFTS = "template_drafts"
# Most recently updated first; the id breaks ties so pages never overlap. Index `org_updated` serves the
# org filter and `updated_at`; ties (same millisecond) are rare and sorted in memory.
_DRAFT_ORDER: list[tuple[str, int]] = [("updated_at", -1), ("_id", 1)]
TEMPLATES = "templates"
VERSIONS = "template_versions"

Document = dict[str, Any]


class VersionExistsError(Exception):
    """A published version was about to be written again: versions are never replaced."""


@dataclass(frozen=True, slots=True)
class StoredDraft:
    id: str
    key: str
    template: Mapping[str, Any]
    issues: tuple[Mapping[str, Any], ...]
    revision: int
    status: str
    published_version: int | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    def document(self) -> Document:
        return {
            "_id": self.id,
            "key": self.key,
            "template": dict(self.template),
            "issues": [dict(issue) for issue in self.issues],
            "revision": self.revision,
            "status": self.status,
            "published_version": self.published_version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> StoredDraft:
        return cls(
            id=str(document["_id"]),
            key=document["key"],
            template=document["template"],
            issues=tuple(document.get("issues", ())),
            revision=document["revision"],
            status=document["status"],
            published_version=document.get("published_version"),
            created_by=document["created_by"],
            created_at=document["created_at"],
            updated_by=document["updated_by"],
            updated_at=document["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class VersionEntry:
    """A version's summary on its template head; the only part of a version that changes (archive)."""

    version: int
    published_at: datetime
    published_by: str
    archived_at: datetime | None = None
    archived_by: str | None = None

    @property
    def archived(self) -> bool:
        return self.archived_at is not None

    def document(self) -> Document:
        return {
            "version": self.version,
            "published_at": self.published_at,
            "published_by": self.published_by,
            "archived_at": self.archived_at,
            "archived_by": self.archived_by,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> VersionEntry:
        return cls(
            version=document["version"],
            published_at=document["published_at"],
            published_by=document["published_by"],
            archived_at=document.get("archived_at"),
            archived_by=document.get("archived_by"),
        )


@dataclass(frozen=True, slots=True)
class TemplateHead:
    id: str
    key: str
    name: str | None
    latest_version: int
    versions: tuple[VersionEntry, ...] = field(default=())

    def entry(self, version: int) -> VersionEntry | None:
        return next((entry for entry in self.versions if entry.version == version), None)

    def with_version(self, entry: VersionEntry, name: str | None) -> TemplateHead:
        return replace(self, name=name, latest_version=entry.version, versions=(*self.versions, entry))

    def document(self) -> Document:
        return {
            "_id": self.id,
            "key": self.key,
            "name": self.name,
            "latest_version": self.latest_version,
            "versions": [entry.document() for entry in self.versions],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> TemplateHead:
        return cls(
            id=str(document["_id"]),
            key=document["key"],
            name=document.get("name"),
            latest_version=document["latest_version"],
            versions=tuple(VersionEntry.from_document(entry) for entry in document.get("versions", ())),
        )


@dataclass(frozen=True, slots=True)
class StoredVersion:
    id: str
    key: str
    version: int
    template: Mapping[str, Any]
    draft_id: str
    published_at: datetime
    published_by: str

    def document(self) -> Document:
        return {
            "_id": self.id,
            "key": self.key,
            "version": self.version,
            "template": dict(self.template),
            "draft_id": self.draft_id,
            "published_at": self.published_at,
            "published_by": self.published_by,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> StoredVersion:
        return cls(
            id=str(document["_id"]),
            key=document["key"],
            version=document["version"],
            template=document["template"],
            draft_id=document["draft_id"],
            published_at=document["published_at"],
            published_by=document["published_by"],
        )


class TemplateStore(Protocol):
    """The operations the lifecycle needs, each in the current tenant scope."""

    async def ensure_indexes(self) -> None: ...

    async def insert_draft(self, draft: StoredDraft) -> None: ...

    async def get_draft(self, draft_id: str) -> StoredDraft | None: ...

    async def list_drafts(self) -> list[StoredDraft]:
        """The org's drafts, most recently updated first."""

    async def page_drafts(self, *, page: int, limit: int) -> tuple[list[StoredDraft], int]:
        """One page of the org's drafts in `list_drafts` order, and how many the org has."""

    async def replace_draft(self, draft: StoredDraft, *, expected_revision: int) -> bool:
        """Store `draft` over the unpublished draft at `expected_revision`; False when there is none."""

    async def get_template(self, key: str) -> TemplateHead | None: ...

    async def list_templates(self) -> list[TemplateHead]:
        """The org's template heads, by key."""

    async def insert_template(self, head: TemplateHead) -> bool:
        """A new head for a key the org has never published; False when the key already has one."""

    async def append_version_entry(
        self, key: str, *, expected_latest: int, entry: VersionEntry, name: str | None
    ) -> bool:
        """Advance the head from `expected_latest` to `entry.version`; False when it has moved on."""

    async def insert_version(self, version: StoredVersion) -> None:
        """Insert-only: `VersionExistsError` when the version is already stored."""

    async def get_version(self, key: str, version: int) -> StoredVersion | None: ...

    async def archive_version(self, key: str, version: int, *, at: datetime, by: str) -> bool:
        """Mark the version archived on its head; False when it is missing or already archived."""

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        """Run `callback` so its writes all happen or none do."""


class MongoTemplateStore:
    __slots__ = ("_store",)

    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(TEMPLATES, [("org_id", 1), ("key", 1)], unique=True, name="org_key")
        await self._store.ensure_index(DRAFTS, [("org_id", 1), ("updated_at", -1)], name="org_updated")
        # A version is written once: a second insert for the same version fails here.
        await self._store.ensure_index(
            VERSIONS, [("org_id", 1), ("key", 1), ("version", 1)], unique=True, name="org_key_version"
        )

    async def insert_draft(self, draft: StoredDraft) -> None:
        await self._store.tenant(DRAFTS).insert_one(draft.document())

    async def get_draft(self, draft_id: str) -> StoredDraft | None:
        found = await self._store.tenant(DRAFTS).find_one({"_id": draft_id})
        return None if found is None else StoredDraft.from_document(found)

    async def list_drafts(self) -> list[StoredDraft]:
        found = await self._store.tenant(DRAFTS).find({}, sort=_DRAFT_ORDER)
        return [StoredDraft.from_document(document) for document in found]

    async def page_drafts(self, *, page: int, limit: int) -> tuple[list[StoredDraft], int]:
        drafts = self._store.tenant(DRAFTS)
        total: int = await drafts.count({})
        found = await drafts.find({}, sort=_DRAFT_ORDER, skip=(page - 1) * limit, limit=limit)
        return [StoredDraft.from_document(document) for document in found], total

    async def replace_draft(self, draft: StoredDraft, *, expected_revision: int) -> bool:
        fields = {k: v for k, v in draft.document().items() if k != "_id"}
        outcome = await self._store.tenant(DRAFTS).update_one(
            {"_id": draft.id, "revision": expected_revision, "status": "draft"}, {"$set": fields}
        )
        return outcome.matched == 1

    async def get_template(self, key: str) -> TemplateHead | None:
        found = await self._store.tenant(TEMPLATES).find_one({"key": key})
        return None if found is None else TemplateHead.from_document(found)

    async def list_templates(self) -> list[TemplateHead]:
        found = await self._store.tenant(TEMPLATES).find({}, sort=[("key", 1)])
        return [TemplateHead.from_document(document) for document in found]

    async def insert_template(self, head: TemplateHead) -> bool:
        try:
            await self._store.tenant(TEMPLATES).insert_one(head.document())
        except DuplicateKeyError:
            return False
        return True

    async def append_version_entry(
        self, key: str, *, expected_latest: int, entry: VersionEntry, name: str | None
    ) -> bool:
        outcome = await self._store.tenant(TEMPLATES).update_one(
            {"key": key, "latest_version": expected_latest},
            {"$set": {"latest_version": entry.version, "name": name}, "$push": {"versions": entry.document()}},
        )
        return outcome.matched == 1

    async def insert_version(self, version: StoredVersion) -> None:
        """Insert-only. `VersionExistsError` when the version is already stored: it is never replaced."""
        try:
            await self._store.tenant(VERSIONS).insert_one(version.document())
        except DuplicateKeyError:
            raise VersionExistsError(f"{version.key} v{version.version} is already published") from None

    async def get_version(self, key: str, version: int) -> StoredVersion | None:
        found = await self._store.tenant(VERSIONS).find_one({"key": key, "version": version})
        return None if found is None else StoredVersion.from_document(found)

    async def archive_version(self, key: str, version: int, *, at: datetime, by: str) -> bool:
        outcome = await self._store.tenant(TEMPLATES).update_one(
            {"key": key, "versions": {"$elemMatch": {"version": version, "archived_at": None}}},
            {"$set": {"versions.$.archived_at": at, "versions.$.archived_by": by}},
        )
        return outcome.matched == 1

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        return await self._store.run_transaction(callback)
