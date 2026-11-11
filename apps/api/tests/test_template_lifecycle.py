"""The lifecycle's rules below HTTP (TPL-2): versions are written once, and a publish or archive whose
audit write fails leaves nothing behind (AUD-1)."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from onb_agents.registry import AgentRegistry
from onb_api.templates import LifecycleConflictError, RegistryValidatorSource, TemplateLifecycle
from onb_api.templates.store import MongoTemplateStore, TemplateStore, VersionExistsError
from onb_api.testing import InMemoryAuditLog, InMemoryTemplateStore
from onb_contracts.templates import Template
from onb_data import Actor, AuditOutcome, NotFoundError, tenant_scope

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = Template.model_validate(yaml.safe_load((ROOT / "templates" / "engineer-india.yaml").read_text()))


class FailingAuditor(InMemoryAuditLog):
    """An audit log whose writes fail for the listed actions."""

    def __init__(self, *failing: str) -> None:
        super().__init__()
        self.failing = frozenset(failing)

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        if action in self.failing:
            raise RuntimeError("audit store unavailable")
        return await super().audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)


def _run[T](make: Callable[[], Awaitable[T]]) -> T:
    async def scoped() -> T:
        with tenant_scope("org_acme"):
            return await make()

    return asyncio.run(scoped())


def _lifecycle(registry: AgentRegistry, store: InMemoryTemplateStore, auditor: InMemoryAuditLog) -> TemplateLifecycle:
    return TemplateLifecycle(store, RegistryValidatorSource.from_config(registry, ROOT / "config"), auditor)


def test_a_stored_version_is_never_written_again(registry: AgentRegistry) -> None:
    store = InMemoryTemplateStore()
    lifecycle = _lifecycle(registry, store, InMemoryAuditLog())

    async def scenario() -> tuple[Any, Any]:
        draft = await lifecycle.create_draft(TEMPLATE, actor_id="u_acme_hr1")
        await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")
        stored = await store.get_version("engineer-india", 1)
        assert stored is not None
        changed = dataclasses.replace(stored, template={**stored.template, "name": "Rewritten"})
        with pytest.raises(VersionExistsError):
            await store.insert_version(changed)
        return stored, await store.get_version("engineer-india", 1)

    before, after = _run(scenario)
    assert after == before


@pytest.mark.parametrize("store_type", [InMemoryTemplateStore, MongoTemplateStore, TemplateStore])
def test_no_store_has_a_way_to_update_or_delete_a_version(store_type: type) -> None:
    names = {name for name in dir(store_type) if not name.startswith("_")}
    version_ops = {n for n in names if "version" in n}
    # Reads, the one insert, the head's summary and the head's archive flag: nothing rewrites a body.
    assert version_ops == {"get_version", "insert_version", "append_version_entry", "archive_version"}
    assert not {n for n in names if n.startswith(("delete", "remove", "update", "upsert"))}


def test_a_version_read_from_the_in_memory_store_is_a_copy(registry: AgentRegistry) -> None:
    store = InMemoryTemplateStore()
    lifecycle = _lifecycle(registry, store, InMemoryAuditLog())

    async def scenario() -> None:
        draft = await lifecycle.create_draft(TEMPLATE, actor_id="u_acme_hr1")
        await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")
        first = await store.get_version("engineer-india", 1)
        assert first is not None
        first.template["name"] = "Changed in the caller's copy"  # type: ignore[index]
        again = await store.get_version("engineer-india", 1)
        assert again is not None
        assert again.template["name"] == "Engineers, India"

    _run(scenario)


@pytest.mark.parametrize("action", ["template.publish"])
def test_a_publish_whose_audit_write_fails_publishes_nothing(registry: AgentRegistry, action: str) -> None:
    store = InMemoryTemplateStore()
    lifecycle = _lifecycle(registry, store, FailingAuditor(action))

    async def scenario() -> None:
        draft = await lifecycle.create_draft(TEMPLATE, actor_id="u_acme_hr1")
        with pytest.raises(RuntimeError, match="audit"):
            await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")
        assert await store.list_templates() == []
        assert await store.get_version("engineer-india", 1) is None
        assert (await lifecycle.get_draft(draft.id)).status == "draft"

    _run(scenario)


def test_an_archive_whose_audit_write_fails_archives_nothing(registry: AgentRegistry) -> None:
    store = InMemoryTemplateStore()
    lifecycle = _lifecycle(registry, store, FailingAuditor("template.archive"))

    async def scenario() -> None:
        draft = await lifecycle.create_draft(TEMPLATE, actor_id="u_acme_hr1")
        await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")
        with pytest.raises(RuntimeError, match="audit"):
            await lifecycle.archive("engineer-india", 1, actor_id="u_acme_hradmin")
        assert (await lifecycle.get_version("engineer-india", 1)).status == "published"

    _run(scenario)


def test_a_concurrent_publish_of_the_same_draft_is_a_conflict(
    registry: AgentRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryTemplateStore()
    lifecycle = _lifecycle(registry, store, InMemoryAuditLog())

    async def scenario() -> None:
        draft = await lifecycle.create_draft(TEMPLATE, actor_id="u_acme_hr1")
        stale = await store.get_draft(draft.id)
        assert stale is not None
        await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")

        # A second publisher that read the draft before the first one locked it.
        async def stale_get(_: str) -> Any:
            return stale

        with monkeypatch.context() as patched:
            patched.setattr(store, "get_draft", stale_get)
            with pytest.raises(LifecycleConflictError):
                await lifecycle.publish(draft.id, actor_id="u_acme_hradmin")
        head = await store.get_template("engineer-india")
        assert head is not None
        assert head.latest_version == 1  # the conflicting publish took no version number
        assert await store.get_version("engineer-india", 2) is None

    _run(scenario)


def test_a_missing_version_is_not_found(registry: AgentRegistry) -> None:
    lifecycle = _lifecycle(registry, InMemoryTemplateStore(), InMemoryAuditLog())
    with pytest.raises(NotFoundError):
        _run(lambda: lifecycle.get_version("engineer-india", 1))
