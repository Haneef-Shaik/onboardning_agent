"""`InMemoryTemplateStore`: `TemplateStore` for tests. Production code never imports it.

It keeps the Mongo store's contract: everything is per org (read and written in the current tenant
scope only), a draft is replaced only at its revision while unpublished, a key has one head per org,
the head advances only from the latest version it holds, a version is inserted once and never
replaced (`VersionExistsError`), and
`atomically` rolls every write back when the callback raises.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from onb_data import current_tenant

from onb_api.templates.store import StoredDraft, StoredVersion, TemplateHead, VersionEntry, VersionExistsError

__all__ = ["InMemoryTemplateStore"]


@dataclass(frozen=True)
class _State:
    drafts: dict[tuple[str, str], StoredDraft] = field(default_factory=dict)
    heads: dict[tuple[str, str], TemplateHead] = field(default_factory=dict)
    versions: dict[tuple[str, str, int], StoredVersion] = field(default_factory=dict)

    def copy(self) -> _State:
        return _State(dict(self.drafts), dict(self.heads), dict(self.versions))


def _org() -> str:
    return current_tenant().org_id


class InMemoryTemplateStore:
    def __init__(self) -> None:
        self._state = _State()
        self.indexes_ensured = 0

    async def ensure_indexes(self) -> None:
        self.indexes_ensured += 1

    async def insert_draft(self, draft: StoredDraft) -> None:
        key = (_org(), draft.id)
        if key in self._state.drafts:
            raise ValueError("a draft with this id exists")
        self._state.drafts[key] = draft

    async def get_draft(self, draft_id: str) -> StoredDraft | None:
        return self._state.drafts.get((_org(), draft_id))

    async def list_drafts(self) -> list[StoredDraft]:
        org = _org()
        own = [draft for (owner, _), draft in self._state.drafts.items() if owner == org]
        return sorted(sorted(own, key=lambda d: d.id), key=lambda d: d.updated_at, reverse=True)

    async def page_drafts(self, *, page: int, limit: int) -> tuple[list[StoredDraft], int]:
        every = await self.list_drafts()
        start = (page - 1) * limit
        return every[start : start + limit], len(every)

    async def replace_draft(self, draft: StoredDraft, *, expected_revision: int) -> bool:
        key = (_org(), draft.id)
        current = self._state.drafts.get(key)
        if current is None or current.revision != expected_revision or current.status != "draft":
            return False
        self._state.drafts[key] = draft
        return True

    async def get_template(self, key: str) -> TemplateHead | None:
        return self._state.heads.get((_org(), key))

    async def list_templates(self) -> list[TemplateHead]:
        org = _org()
        return sorted((head for (owner, _), head in self._state.heads.items() if owner == org), key=lambda h: h.key)

    async def insert_template(self, head: TemplateHead) -> bool:
        key = (_org(), head.key)
        if key in self._state.heads:
            return False
        self._state.heads[key] = head
        return True

    async def append_version_entry(
        self, key: str, *, expected_latest: int, entry: VersionEntry, name: str | None
    ) -> bool:
        head = self._state.heads.get((_org(), key))
        if head is None or head.latest_version != expected_latest:
            return False
        self._state.heads[(_org(), key)] = head.with_version(entry, name)
        return True

    async def insert_version(self, version: StoredVersion) -> None:
        key = (_org(), version.key, version.version)
        if key in self._state.versions:
            raise VersionExistsError(f"{version.key} v{version.version} is already published")
        self._state.versions[key] = version

    async def get_version(self, key: str, version: int) -> StoredVersion | None:
        found = self._state.versions.get((_org(), key, version))
        # A copy, like a Mongo read: a caller changing what it got cannot change the stored version.
        return None if found is None else replace(found, template=copy.deepcopy(dict(found.template)))

    async def archive_version(self, key: str, version: int, *, at: datetime, by: str) -> bool:
        head = self._state.heads.get((_org(), key))
        entry = None if head is None else head.entry(version)
        if head is None or entry is None or entry.archived:
            return False
        archived = replace(entry, archived_at=at, archived_by=by)
        versions = tuple(archived if e.version == version else e for e in head.versions)
        self._state.heads[(_org(), key)] = replace(head, versions=versions)
        return True

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        before = self._state.copy()
        try:
            return await callback()
        except BaseException:
            self._state = before
            raise
