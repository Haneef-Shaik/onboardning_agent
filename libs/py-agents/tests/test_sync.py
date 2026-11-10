"""Syncing manifests into the registry: versioned and audited (REG-1 box 2; PRD FR-A1).

A sync of unchanged files writes and audits nothing. Changing a YAML and syncing again bumps that
agent's version and writes one audit record per org. Writes and audits land together or not at all.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from onb_agents.registry import (
    AgentRegistry,
    ManifestError,
    RegistrySyncError,
    SyncReport,
    ToolCatalog,
    load_catalog,
    load_manifests,
)
from onb_agents.testing import InMemoryRegistryStore
from onb_data import Actor, ActorType, AuditOutcome, AuditQuery, tenant_scope
from onb_data.testing import InMemoryAuditLog

ROOT = Path(__file__).resolve().parents[3]
ADMIN = Actor.user("u_acme_hradmin")
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def run[T](coro: Awaitable[T]) -> T:
    async def wrapped() -> T:
        return await coro

    return asyncio.run(wrapped())


@pytest.fixture
def config(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "config" / "agents", tmp_path / "agents")
    shutil.copy(ROOT / "config" / "tool_catalog.yaml", tmp_path / "tool_catalog.yaml")
    return tmp_path


@pytest.fixture
def store() -> InMemoryRegistryStore:
    return InMemoryRegistryStore()


@pytest.fixture
def log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def registry(store: InMemoryRegistryStore, log: InMemoryAuditLog) -> AgentRegistry:
    return AgentRegistry(store, log)


def _sync(registry: AgentRegistry, config: Path, *, now: datetime = T0) -> SyncReport:
    manifests = load_manifests(config / "agents")
    catalog = load_catalog(config / "tool_catalog.yaml", manifests)
    return run(registry.sync(manifests, catalog, actor=ADMIN, now=now))


def _records(log: InMemoryAuditLog, org: str, **query: object) -> list[tuple[str, str, str | None]]:
    async def read() -> list[tuple[str, str, str | None]]:
        with tenant_scope(org):
            page = await log.list(AuditQuery(**query), page=1, limit=200)  # type: ignore[arg-type]  # test filters
        return sorted((e.action, e.target, e.reason) for e in page.entries)

    return run(read())


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


SIX_AGENTS = (
    "approvals_agent",
    "db_agent",
    "provisioning_agent",
    "run_assistant",
    "template_builder",
    "templates_agent",
)


def test_the_first_sync_registers_every_agent_at_version_1_and_audits_it(
    registry: AgentRegistry, config: Path, log: InMemoryAuditLog
) -> None:
    report = _sync(registry, config)
    assert {(c.kind, c.name, c.version) for c in report.changes} == {("register", name, 1) for name in SIX_AGENTS} | {
        ("catalog_update", "tool_catalog", 1)
    }
    agents = run(registry.list_agents())
    assert [(a.name, a.version, a.active) for a in agents] == [(name, 1, True) for name in SIX_AGENTS]
    db_agent = run(registry.get_agent("db_agent"))
    assert db_agent is not None
    assert report.audited_orgs == ("org_acme", "org_globex")
    for org in report.audited_orgs:
        actions = _records(log, org)
        assert ("registry.agent_register", "agent:db_agent/v1", f"sha256:{db_agent.sha256[:16]}") in actions
        assert len(actions) == 7


# --- box 2: changing a YAML and re-syncing bumps the version and writes an audit record ----------


def test_changing_a_yaml_and_resyncing_bumps_the_version_and_writes_an_audit_record(
    registry: AgentRegistry, config: Path, log: InMemoryAuditLog
) -> None:
    _sync(registry, config)
    before = run(registry.get_agent("db_agent"))
    assert before is not None
    assert before.version == 1

    _edit(config / "agents" / "db_agent.yaml", "max_steps: 8", "max_steps: 6")
    later = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    report = _sync(registry, config, now=later)

    after = run(registry.get_agent("db_agent"))
    assert after is not None
    assert after.version == 2
    assert after.manifest.limits.max_steps == 6
    assert after.sha256 != before.sha256
    assert after.updated_at == later
    assert [(c.kind, c.name, c.version) for c in report.changes] == [("update", "db_agent", 2)]
    for org in ("org_acme", "org_globex"):
        updates = _records(log, org, action="registry.agent_update")
        assert updates == [("registry.agent_update", "agent:db_agent/v2", f"sha256:{after.sha256[:16]}")]
    with tenant_scope("org_acme"):
        entry = run(log.list(AuditQuery(action="registry.agent_update"), page=1, limit=1)).entries[0]
    assert (entry.actor.type, entry.actor.id, entry.outcome) == (ActorType.USER, ADMIN.id, AuditOutcome.SUCCESS)
    others = {a.name: a.version for a in run(registry.list_agents()) if a.name != "db_agent"}
    assert set(others.values()) == {1}


def test_resyncing_unchanged_files_writes_and_audits_nothing(
    registry: AgentRegistry, config: Path, log: InMemoryAuditLog, store: InMemoryRegistryStore
) -> None:
    _sync(registry, config)
    _edit(config / "agents" / "db_agent.yaml", "# DB agent", "# The DB agent")  # a comment only
    report = _sync(registry, config)
    assert report.changes == ()
    assert len(report.unchanged) == 6
    assert len(_records(log, "org_acme")) == 7
    assert store.transactions == 1


def test_a_catalog_change_bumps_the_catalog_version(
    registry: AgentRegistry, config: Path, log: InMemoryAuditLog
) -> None:
    _sync(registry, config)
    _edit(config / "tool_catalog.yaml", "{ action: provision_laptop, agent: provisioning_agent }", "")
    _edit(config / "tool_catalog.yaml", "  - \n", "")
    report = _sync(registry, config)
    assert [(c.kind, c.target) for c in report.changes] == [("catalog_update", "catalog:tool_catalog/v2")]
    assert _records(log, "org_globex", action="registry.catalog_update")[-1][1] == "catalog:tool_catalog/v2"


def test_a_removed_manifest_retires_the_agent_and_a_restored_one_comes_back(
    registry: AgentRegistry, config: Path, log: InMemoryAuditLog
) -> None:
    _sync(registry, config)
    saved = (config / "agents" / "run_assistant.yaml").read_text()
    (config / "agents" / "run_assistant.yaml").unlink()
    report = _sync(registry, config)
    assert [(c.kind, c.name, c.version) for c in report.changes] == [("retire", "run_assistant", 2)]
    retired = run(registry.get_agent("run_assistant"))
    assert retired is not None
    assert not retired.active
    assert _records(log, "org_acme", action="registry.agent_retire") == [
        ("registry.agent_retire", "agent:run_assistant/v2", f"sha256:{retired.sha256[:16]}")
    ]

    (config / "agents" / "run_assistant.yaml").write_text(saved)
    report = _sync(registry, config)
    assert [(c.kind, c.name, c.version) for c in report.changes] == [("update", "run_assistant", 3)]
    restored = run(registry.get_agent("run_assistant"))
    assert restored is not None
    assert restored.active


# --- failure paths -------------------------------------------------------------------------------


def test_a_change_with_no_org_to_audit_in_is_refused_and_stores_nothing(log: InMemoryAuditLog, config: Path) -> None:
    store = InMemoryRegistryStore(org_ids=())
    registry = AgentRegistry(store, log)
    with pytest.raises(RegistrySyncError, match="no org"):
        _sync(registry, config)
    assert run(registry.list_agents()) == []


class FailingAuditLog(InMemoryAuditLog):
    """Fails on the n-th audit write, like a database error mid-sync."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self.fail_on = fail_on
        self.calls = 0

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        self.calls += 1
        if self.calls == self.fail_on:
            raise ConnectionError("audit write failed")
        return await super().audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)


def test_a_failed_audit_write_stores_no_registry_change(store: InMemoryRegistryStore, config: Path) -> None:
    registry = AgentRegistry(store, FailingAuditLog(fail_on=4))
    with pytest.raises(ConnectionError, match="audit write failed"):
        _sync(registry, config)
    assert run(registry.list_agents()) == []
    assert run(store.catalog()) is None


def test_a_concurrent_change_is_refused_not_overwritten(
    store: InMemoryRegistryStore, log: InMemoryAuditLog, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = AgentRegistry(store, log)
    _sync(registry, config)
    _edit(config / "agents" / "db_agent.yaml", "max_steps: 8", "max_steps: 7")
    manifests = load_manifests(config / "agents")
    catalog = load_catalog(config / "tool_catalog.yaml", manifests)

    original: Callable[[Callable[[], Awaitable[object]]], Awaitable[object]] = store.atomically

    async def racing(callback: Callable[[], Awaitable[object]]) -> object:
        current = await store.agent("db_agent")
        assert current is not None
        await store.replace_agent("db_agent", 1, {**current, "version": 2})  # another sync won
        return await original(callback)

    monkeypatch.setattr(store, "atomically", racing)  # another sync commits first
    with pytest.raises(RegistrySyncError, match="changed during the sync"):
        run(registry.sync(manifests, catalog, actor=ADMIN))
    stored = run(registry.get_agent("db_agent"))
    assert stored is not None
    assert stored.manifest.limits.max_steps == 8


def test_two_manifests_with_one_name_are_refused(registry: AgentRegistry, config: Path) -> None:
    manifests = load_manifests(config / "agents")
    catalog = load_catalog(config / "tool_catalog.yaml", manifests)
    with pytest.raises(RegistrySyncError, match="two manifests name db_agent"):
        run(registry.sync([*manifests, manifests[1]], catalog, actor=ADMIN))


def test_a_tampered_stored_manifest_does_not_load(
    registry: AgentRegistry, store: InMemoryRegistryStore, config: Path
) -> None:
    _sync(registry, config)
    stored = run(store.agent("db_agent"))
    assert stored is not None
    forged = {**stored["manifest"], "mcp": {"mongo": {"tools": ["find"], "collections": {"read": ["pii_vault"]}}}}
    store.tamper("db_agent", forged)
    with pytest.raises(ManifestError, match="vault"):
        run(registry.get_agent("db_agent"))


def test_the_catalog_reports_its_entries(config: Path) -> None:
    catalog: ToolCatalog = load_catalog(config / "tool_catalog.yaml", load_manifests(config / "agents"))
    assert len(catalog.sha256()) == 64
