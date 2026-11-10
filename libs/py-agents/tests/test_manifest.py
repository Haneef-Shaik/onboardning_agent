"""`AgentManifest`: the validated shape of one agent's allowlist (REG-1 box 1; PRD §5.2, §7.3, §7.4).

The vault (`pii_vault*`), `org_keys` and `pii_access_log` can never be granted: a manifest that names
any of them anywhere is refused, whatever the field or spelling.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from onb_agents.registry import AgentManifest, ManifestError, parse_manifest
from onb_agents.registry.manifest import FORBIDDEN_NAMES, MONGO_SERVER, names_a_forbidden_collection


def _valid() -> dict[str, Any]:
    return {
        "name": "db_agent",
        "purpose": "Read and update onboarding records for a single run",
        "llm": {"alias": "standard"},
        "mcp": {
            "mongo": {
                "tools": ["find", "aggregate", "count", "update_one"],
                "collections": {
                    "read": ["new_hires", "run_tasks", "documents_meta"],
                    "write": ["run_tasks", "documents_meta"],
                },
            }
        },
        "tools": ["date_calc"],
        "memory_scopes": ["run_state:read", "knowledge:hr_policies"],
        "requires_approval": ["update_one:documents_meta"],
        "limits": {"max_steps": 8, "max_tokens": 20000, "timeout_s": 60},
    }


def _with(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(_valid())
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


def test_the_prd_shaped_manifest_is_accepted() -> None:
    manifest = parse_manifest(_valid())
    assert isinstance(manifest, AgentManifest)
    collections = manifest.mcp[MONGO_SERVER].collections
    assert collections is not None
    assert collections.write == ("run_tasks", "documents_meta")


VAULT_NAMES = ["pii_vault", "pii_vault_aadhaar", "org_keys", "pii_access_log", "PII_VAULT", "pii_vault_v2"]


# --- box 1: a manifest listing a vault collection is rejected ------------------------------------


@pytest.mark.parametrize("collection", VAULT_NAMES)
def test_a_manifest_listing_a_vault_collection_to_read_is_rejected(collection: str) -> None:
    data = _with(("mcp", "mongo", "collections", "read"), ["run_tasks", collection])
    with pytest.raises(ManifestError, match="vault"):
        parse_manifest(data)


@pytest.mark.parametrize("collection", VAULT_NAMES)
def test_a_manifest_listing_a_vault_collection_to_write_is_rejected(collection: str) -> None:
    data = _with(("mcp", "mongo", "collections"), {"read": ["run_tasks", collection], "write": [collection]})
    with pytest.raises(ManifestError, match="vault"):
        parse_manifest(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("requires_approval",), ["update_one:pii_vault"]),
        (("memory_scopes",), ["pii_access_log:read"]),
        (("tools",), ["org_keys"]),
        (("purpose",), "Reads pii_vault_aadhaar for the hire"),
        (("mcp",), {"pii_vault": {"tools": ["find"]}}),
    ],
    ids=["approval", "memory", "local-tool", "purpose", "server-name"],
)
def test_a_vault_name_anywhere_in_the_manifest_is_rejected(path: tuple[str, ...], value: Any) -> None:
    with pytest.raises(ManifestError, match="vault"):
        parse_manifest(_with(path, value))


def test_the_forbidden_names_are_the_vault_the_keys_and_the_access_log() -> None:
    assert FORBIDDEN_NAMES == ("pii_vault", "org_keys", "pii_access_log")
    assert names_a_forbidden_collection("db.pii_vault_aadhaar")
    assert not names_a_forbidden_collection("piivault orgkeys pii_access")
    assert not names_a_forbidden_collection("run_tasks")


# --- the rest of the shape -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("name",), "DB Agent", "name"),
        (("llm",), {"alias": "standard", "model": "x"}, "alias"),
        (("llm",), {"connector": "bedrock"}, "alias"),
        (("llm",), {"alias": "standard", "params": {"region_name": "x"}}, "params"),
        (("mcp", "mongo", "tools"), ["find", "drop"], "drop"),
        (("mcp", "mongo", "tools"), [], "tool"),
        (("mcp", "mongo", "tools"), ["find", "find"], "repeat"),
        (("mcp", "mongo", "collections", "write"), ["run_tasks", "onboarding_runs"], "read"),
        (("mcp", "mongo", "collections"), {"read": ["run_tasks"], "write": []}, "write"),
        (("mcp", "mongo", "collections"), {"read": ["users"], "write": []}, "users"),
        (("mcp", "mongo", "collections"), {"read": ["audit_log"], "write": []}, "audit_log"),
        (("mcp", "mongo", "collections"), {"read": ["agent_registry"], "write": []}, "agent_registry"),
        (("mcp", "mongo", "collections"), {"read": ["jobs"], "write": []}, "jobs"),
        (("mcp", "mocks"), {"tools": ["create_bitbucket_account"], "collections": {"read": ["run_tasks"]}}, "mongo"),
        (("requires_approval",), ["update_one:new_hires"], "write"),
        (("requires_approval",), ["insert_one:run_tasks"], "granted"),
        (("requires_approval",), ["publish_template"], "granted"),
        (("memory_scopes",), ["run_state"], "memory"),
        (("limits",), {"max_steps": 0, "max_tokens": 20000, "timeout_s": 60}, "max_steps"),
        (("limits",), {"max_steps": 8, "max_tokens": 20000}, "timeout_s"),
        (("version",), 3, "version"),
        (("org_id",), "org_acme", "org_id"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_an_invalid_manifest_is_rejected_with_the_reason(path: tuple[str, ...], value: Any, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse_manifest(_with(path, value))


def test_a_manifest_that_is_not_a_mapping_is_rejected() -> None:
    with pytest.raises(ManifestError, match="mapping"):
        parse_manifest(["db_agent"])


def test_requires_approval_may_name_a_granted_tool_on_any_server() -> None:
    data = _with(("mcp", "mocks"), {"tools": ["create_bitbucket_account"]})
    data["requires_approval"] = ["create_bitbucket_account"]
    assert parse_manifest(data).requires_approval == ("create_bitbucket_account",)


def test_the_manifest_is_frozen_and_dumps_back_to_the_same_data() -> None:
    manifest = parse_manifest(_valid())
    with pytest.raises(ValueError, match="frozen"):
        manifest.name = "other"
    assert parse_manifest(manifest.document()) == manifest


def test_the_content_hash_ignores_key_order_and_changes_with_content() -> None:
    reordered = dict(reversed(list(_valid().items())))
    assert parse_manifest(reordered).sha256() == parse_manifest(_valid()).sha256()
    assert parse_manifest(_with(("limits", "max_steps"), 9)).sha256() != parse_manifest(_valid()).sha256()
