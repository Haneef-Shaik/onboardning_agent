"""`AgentRegistry`: sync manifests into `agent_registry`, versioned and audited (FR-A1), and read them back.

A sync compares each manifest's content hash with the stored one:
- a new agent is stored at version 1 (`registry.agent_register`);
- a changed manifest bumps the version (`registry.agent_update`);
- an active agent with no manifest any more is retired, also a new version (`registry.agent_retire`),
  so a removed file stops granting anything;
- a changed tool catalog bumps the catalog's version (`registry.catalog_update`);
- an unchanged manifest writes nothing and audits nothing.

The registry is global, but the audit log is per org: every change is audited in every org's log
(the orgs in `orgs`), so each org's HR_ADMIN sees what their agents may now do. A change with no org
to audit it in is refused. Writes and their audit records run in one transaction: if any audit write
fails, nothing is stored (fail closed, as AUD-1 requires). A concurrent sync that got there first makes
the version check fail (`RegistrySyncError`) instead of overwriting it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from onb_data import Actor, Auditor, AuditOutcome, tenant_scope

from .catalog import ToolCatalog
from .manifest import AgentManifest, parse_manifest

__all__ = [
    "CATALOG_ID",
    "AgentRegistry",
    "RegisteredAgent",
    "RegistryStore",
    "RegistrySyncError",
    "SyncChange",
    "SyncReport",
]

CATALOG_ID = "tool_catalog"

ChangeKind = Literal["register", "update", "retire", "catalog_update"]
_ACTIONS: dict[ChangeKind, str] = {
    "register": "registry.agent_register",
    "update": "registry.agent_update",
    "retire": "registry.agent_retire",
    "catalog_update": "registry.catalog_update",
}

Document = dict[str, Any]


class RegistrySyncError(RuntimeError):
    """A sync that cannot be applied: duplicate names, no org to audit in, or a concurrent change."""


class RegistryStore(Protocol):
    """Where the registry lives. `MongoRegistryStore` in the service, `InMemoryRegistryStore` in tests."""

    async def org_ids(self) -> list[str]: ...

    async def agents(self) -> list[Document]: ...

    async def agent(self, name: str) -> Document | None: ...

    async def insert_agent(self, document: Document) -> None: ...

    async def replace_agent(self, name: str, expected_version: int, document: Document) -> bool:
        """Store `document` only if the stored version is still `expected_version`."""
        ...

    async def catalog(self) -> Document | None: ...

    async def put_catalog(self, expected_version: int | None, document: Document) -> bool:
        """Insert (`expected_version` None) or replace at that version; False when someone else won."""
        ...

    async def atomically[T](self, callback: Callable[[], Awaitable[T]]) -> T:
        """Run `callback` so that all its writes, audit records included, land together or not at all."""
        ...


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    """One stored agent. `manifest` is re-validated on read, so a tampered record cannot load."""

    name: str
    version: int
    sha256: str
    active: bool
    manifest: AgentManifest
    updated_at: datetime

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> RegisteredAgent:
        return cls(
            name=str(document["_id"]),
            version=int(document["version"]),
            sha256=str(document["sha256"]),
            active=bool(document["active"]),
            manifest=parse_manifest(document["manifest"]),
            updated_at=document["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class SyncChange:
    kind: ChangeKind
    name: str
    version: int
    sha256: str

    @property
    def action(self) -> str:
        return _ACTIONS[self.kind]

    @property
    def target(self) -> str:
        kind = "catalog" if self.kind == "catalog_update" else "agent"
        return f"{kind}:{self.name}/v{self.version}"


@dataclass(frozen=True, slots=True)
class SyncReport:
    changes: tuple[SyncChange, ...]
    unchanged: tuple[str, ...]
    audited_orgs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Write:
    change: SyncChange
    expected_version: int | None
    document: Document


def _agent_document(manifest: AgentManifest, *, version: int, active: bool, now: datetime) -> Document:
    return {
        "_id": manifest.name,
        "version": version,
        "sha256": manifest.sha256(),
        "active": active,
        "manifest": manifest.document(),
        "updated_at": now,
    }


class AgentRegistry:
    def __init__(self, store: RegistryStore, auditor: Auditor) -> None:
        self._store = store
        self._auditor = auditor

    async def list_agents(self) -> list[RegisteredAgent]:
        """Every registered agent, retired ones included, by name."""
        documents = await self._store.agents()
        return sorted((RegisteredAgent.from_document(d) for d in documents), key=lambda a: a.name)

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        document = await self._store.agent(name)
        return None if document is None else RegisteredAgent.from_document(document)

    async def sync(
        self,
        manifests: Sequence[AgentManifest],
        catalog: ToolCatalog,
        *,
        actor: Actor,
        now: datetime | None = None,
    ) -> SyncReport:
        names = [m.name for m in manifests]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise RegistrySyncError(f"two manifests name {', '.join(repeated)}")
        stamp = now or datetime.now(UTC)
        writes, unchanged = await self._plan(manifests, catalog, stamp)
        if not writes:
            return SyncReport(changes=(), unchanged=tuple(unchanged), audited_orgs=())
        orgs = tuple(sorted(await self._store.org_ids()))
        if not orgs:
            raise RegistrySyncError("no org to audit the change in: seed `orgs` before syncing the registry")

        async def apply() -> None:
            for write in writes:
                await self._write(write)
                await self._audit(write.change, orgs, actor)

        await self._store.atomically(apply)
        return SyncReport(changes=tuple(w.change for w in writes), unchanged=tuple(unchanged), audited_orgs=orgs)

    async def _plan(
        self, manifests: Sequence[AgentManifest], catalog: ToolCatalog, now: datetime
    ) -> tuple[list[_Write], list[str]]:
        stored = {str(d["_id"]): d for d in await self._store.agents()}
        writes: list[_Write] = []
        unchanged: list[str] = []
        for manifest in sorted(manifests, key=lambda m: m.name):
            current = stored.get(manifest.name)
            if current is None:
                document = _agent_document(manifest, version=1, active=True, now=now)
                writes.append(_Write(SyncChange("register", manifest.name, 1, document["sha256"]), None, document))
            elif current["sha256"] != manifest.sha256() or not current["active"]:
                version = int(current["version"]) + 1
                document = _agent_document(manifest, version=version, active=True, now=now)
                change = SyncChange("update", manifest.name, version, document["sha256"])
                writes.append(_Write(change, int(current["version"]), document))
            else:
                unchanged.append(manifest.name)
        listed = {m.name for m in manifests}
        for name, current in sorted(stored.items()):
            if name not in listed and current["active"]:
                version = int(current["version"]) + 1
                document = {**current, "version": version, "active": False, "updated_at": now}
                change = SyncChange("retire", name, version, str(current["sha256"]))
                writes.append(_Write(change, int(current["version"]), document))
        writes.extend(await self._plan_catalog(catalog, now))
        return writes, unchanged

    async def _plan_catalog(self, catalog: ToolCatalog, now: datetime) -> list[_Write]:
        current = await self._store.catalog()
        if current is not None and current["sha256"] == catalog.sha256():
            return []
        expected = None if current is None else int(current["version"])
        version = 1 if expected is None else expected + 1
        document = {
            "_id": CATALOG_ID,
            "version": version,
            "sha256": catalog.sha256(),
            "actions": catalog.entries(),
            "updated_at": now,
        }
        return [_Write(SyncChange("catalog_update", CATALOG_ID, version, catalog.sha256()), expected, document)]

    async def _write(self, write: _Write) -> None:
        change = write.change
        if change.kind == "catalog_update":
            stored = await self._store.put_catalog(write.expected_version, write.document)
        elif write.expected_version is None:
            await self._store.insert_agent(write.document)
            stored = True
        else:
            stored = await self._store.replace_agent(change.name, write.expected_version, write.document)
        if not stored:
            raise RegistrySyncError(f"{change.name} changed during the sync; run it again")

    async def _audit(self, change: SyncChange, orgs: Sequence[str], actor: Actor) -> None:
        for org in orgs:
            with tenant_scope(org):
                await self._auditor.audit(
                    actor=actor,
                    action=change.action,
                    target=change.target,
                    outcome=AuditOutcome.SUCCESS,
                    reason=f"sha256:{change.sha256[:16]}",
                )
