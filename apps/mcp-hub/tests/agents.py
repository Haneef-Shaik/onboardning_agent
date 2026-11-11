"""Registered agents for hub tests: the real manifests from config/agents, plus forged ones."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from onb_agents.registry import RegisteredAgent, load_manifests
from onb_agents.registry.manifest import AgentManifest, CollectionGrant, Limits, ManifestLLM, McpGrant
from onb_agents.tokens import AgentClaims

ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = {m.name: m for m in load_manifests(ROOT / "config" / "agents")}
NOW = datetime(2026, 11, 11, tzinfo=UTC)


def registered(name: str, *, active: bool = True) -> RegisteredAgent:
    manifest = MANIFESTS[name]
    return RegisteredAgent(name, 1, manifest.sha256(), active, manifest, NOW)


def forged(name: str, *, read: tuple[str, ...], write: tuple[str, ...] = (), tools: tuple[str, ...]) -> RegisteredAgent:
    """A manifest that skipped validation (as if the registry record had been forged): it may name the vault."""
    manifest = AgentManifest.model_construct(
        name=name,
        purpose="forged",
        llm=ManifestLLM.model_construct(alias="standard"),
        mcp={
            "mongo": McpGrant.model_construct(
                tools=tools, collections=CollectionGrant.model_construct(read=read, write=write)
            )
        },
        tools=(),
        memory_scopes=(),
        requires_approval=(),
        limits=Limits.model_construct(max_steps=8, max_tokens=1000, timeout_s=60),
    )
    return RegisteredAgent(name, 1, "0" * 64, True, manifest, NOW)


def with_approval(name: str, rules: tuple[str, ...]) -> RegisteredAgent:
    manifest = MANIFESTS[name].model_copy(update={"requires_approval": rules})
    return RegisteredAgent(name, 1, manifest.sha256(), True, manifest, NOW)


def claims(agent: str = "db_agent", *, org: str = "org_acme", scopes: tuple[str, ...] | None = None) -> AgentClaims:
    if scopes is None:
        grant = MANIFESTS[agent].mcp
        scopes = tuple(f"{server}__{tool}" for server, g in grant.items() for tool in g.tools)
    return AgentClaims(agent=agent, run_id="run_1", org_id=org, scopes=scopes)
