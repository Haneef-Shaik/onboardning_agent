"""Regression tests for the REG-1 review findings (security review CRITICAL, code review HIGH and others).

- A vault name encoded as YAML `!!binary` (or any non-text value) is refused, not coerced.
- Collections are granted from an allowlist that equals the MCP database users' sets (MCP-3).
- LLM params are typed and bounded, and cannot exceed the agent's token limit.
- A stored record whose manifest no longer matches its hash or name does not load.
- A concurrent first registration loses cleanly (`RegistrySyncError`), not with a driver error.
"""

from __future__ import annotations

import asyncio
import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
from onb_agents.registry import AgentRegistry, ManifestError, RegistrySyncError, load_catalog, load_manifests
from onb_agents.registry import parse_manifest as parse
from onb_agents.registry.manifest import AGENT_READABLE_COLLECTIONS, AGENT_WRITABLE_COLLECTIONS
from onb_agents.testing import InMemoryRegistryStore
from onb_data import Actor
from onb_data.testing import InMemoryAuditLog

ROOT = Path(__file__).resolve().parents[3]
DB_AGENT = ROOT / "config" / "agents" / "db_agent.yaml"


def _valid() -> dict[str, Any]:
    return {
        "name": "db_agent",
        "purpose": "Read and update onboarding records",
        "llm": {"alias": "standard"},
        "mcp": {
            "mongo": {
                "tools": ["find", "update_one"],
                "collections": {"read": ["run_tasks", "documents_meta"], "write": ["run_tasks"]},
            }
        },
        "limits": {"max_steps": 8, "max_tokens": 20000, "timeout_s": 60},
    }


def _grant(read: list[Any], write: list[Any] | None = None) -> dict[str, Any]:
    data = copy.deepcopy(_valid())
    data["mcp"]["mongo"]["collections"] = {"read": read, "write": write or []}
    if not write:
        data["mcp"]["mongo"]["tools"] = ["find"]
    return data


# --- CRITICAL: non-text values cannot smuggle a vault name ---------------------------------------


@pytest.mark.parametrize("encoded", ["cGlpX3ZhdWx0", "b3JnX2tleXM=", "cGlpX2FjY2Vzc19sb2c="])
def test_a_vault_name_as_yaml_binary_is_rejected(tmp_path: Path, encoded: str) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    text = DB_AGENT.read_text().replace(
        "documents_meta]\n      write", f"documents_meta, !!binary {encoded}]\n      write"
    )
    assert "!!binary" in text
    (agents / "db_agent.yaml").write_text(text)
    with pytest.raises(ManifestError, match="vault"):
        load_manifests(agents)


@pytest.mark.parametrize("value", [b"pii_vault", b"run_tasks", 7, 1.5, True])
def test_a_collection_that_is_not_text_is_rejected(value: Any) -> None:
    with pytest.raises(ManifestError):
        parse(_grant(["documents_meta", value]))


@pytest.mark.parametrize("text", ["copy x_pii_vault rows", "see old_org_keys", "tmp_pii_access_log_2"])
def test_a_name_with_the_vault_inside_it_is_rejected(text: str) -> None:
    data = _valid()
    data["purpose"] = text  # free text: only the vault check, not the collection allowlist, can refuse it
    with pytest.raises(ManifestError, match="may not name a vault collection"):
        parse(data)


# --- HIGH: collections come from an allowlist ----------------------------------------------------


@pytest.mark.parametrize("collection", ["checkpoints", "run_task", "events", "mock_outbox", "eval_runs"])
def test_a_collection_outside_the_allowlist_cannot_be_read(collection: str) -> None:
    with pytest.raises(ManifestError, match=f"{collection} cannot be granted"):
        parse(_grant(["run_tasks", collection]))


@pytest.mark.parametrize("collection", ["approvals", "templates", "template_versions", "messages"])
def test_a_collection_outside_the_write_allowlist_cannot_be_written(collection: str) -> None:
    with pytest.raises(ManifestError, match=f"{collection} cannot be written"):
        parse(_grant(["run_tasks", collection], [collection]))


def test_the_allowlists_equal_the_mcp_database_users_sets() -> None:
    from onb_mcp_mongo.db_users import DENIED_COLLECTIONS, READ_ONLY, READ_WRITE

    assert set(READ_ONLY.collections) == AGENT_READABLE_COLLECTIONS
    assert set(READ_WRITE.collections) == AGENT_WRITABLE_COLLECTIONS
    assert not AGENT_READABLE_COLLECTIONS & DENIED_COLLECTIONS


# --- MEDIUM: typed, bounded LLM params -----------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"temperature": "hot"},
        {"temperature": 3},
        {"max_tokens": 1_000_000_000},
        {"max_tokens": 30000},  # above this agent's limits.max_tokens
        {"top_p": 0},
        {"stop": ["a", "b", "c", "d", "e"]},
        {"region_name": "x"},
    ],
)
def test_llm_params_are_typed_and_bounded(params: dict[str, Any]) -> None:
    data = _valid()
    data["llm"] = {"alias": "standard", "params": params}
    with pytest.raises(ManifestError, match="params"):
        parse(data)


def test_valid_llm_params_are_kept_and_frozen() -> None:
    data = _valid()
    data["llm"] = {"alias": "standard", "params": {"temperature": 0.2, "max_tokens": 4000, "stop": ["END"]}}
    manifest = parse(data)
    assert manifest.llm.params.values() == {"temperature": 0.2, "max_tokens": 4000, "stop": ["END"]}
    with pytest.raises(ValueError, match="frozen"):
        manifest.llm.params.temperature = 1.0


# --- LOW: stored records are checked on read; a lost insert race is a clean refusal --------------


def _synced(tmp_path: Path) -> tuple[AgentRegistry, InMemoryRegistryStore, Path]:
    shutil.copytree(ROOT / "config" / "agents", tmp_path / "agents")
    shutil.copy(ROOT / "config" / "tool_catalog.yaml", tmp_path / "tool_catalog.yaml")
    store = InMemoryRegistryStore()
    registry = AgentRegistry(store, InMemoryAuditLog())
    manifests = load_manifests(tmp_path / "agents")
    catalog = load_catalog(tmp_path / "tool_catalog.yaml", manifests)
    asyncio.run(registry.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin")))
    return registry, store, tmp_path


def test_a_stored_manifest_widened_behind_the_registry_does_not_load(tmp_path: Path) -> None:
    registry, store, _ = _synced(tmp_path)
    stored = asyncio.run(store.agent("run_assistant"))
    assert stored is not None
    widened = copy.deepcopy(stored["manifest"])
    widened["mcp"]["mongo"]["collections"]["read"].append("new_hires")  # still a valid manifest
    store.tamper("run_assistant", widened)
    with pytest.raises(ManifestError, match="does not match its manifest hash"):
        asyncio.run(registry.get_agent("run_assistant"))


def test_a_stored_manifest_under_another_name_does_not_load(tmp_path: Path) -> None:
    registry, store, _ = _synced(tmp_path)
    other = asyncio.run(store.agent("db_agent"))
    assert other is not None
    store.tamper("run_assistant", other["manifest"])
    with pytest.raises(ManifestError, match="does not match"):
        asyncio.run(registry.get_agent("run_assistant"))


def test_a_concurrent_first_registration_is_refused_cleanly(tmp_path: Path) -> None:
    registry, store, config = _synced(tmp_path)
    new = (config / "agents" / "db_agent.yaml").read_text().replace("name: db_agent", "name: db_agent_two")
    (config / "agents" / "db_agent_two.yaml").write_text(new)
    manifests = load_manifests(config / "agents")
    catalog = load_catalog(config / "tool_catalog.yaml", manifests)
    winner = manifests[2]
    assert winner.name == "db_agent_two"
    document = {"_id": "db_agent_two", "version": 1, "sha256": winner.sha256(), "active": True}
    asyncio.run(store.insert_agent({**document, "manifest": winner.document(), "updated_at": None}))

    async def race() -> None:
        # The plan was made before the other sync's insert landed.
        await registry.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin"))

    store_agents = store.agents

    async def before_insert() -> list[dict[str, Any]]:
        return [d for d in await store_agents() if d["_id"] != "db_agent_two"]

    store.agents = before_insert  # type: ignore[method-assign]  # the planning read misses the insert
    with pytest.raises(RegistrySyncError, match="changed during the sync"):
        asyncio.run(race())
