"""Agent registry contracts (PRD U8, FR-A1, §7.3, §10 `GET /agents`, HR_ADMIN only).

One record per registered agent: its manifest (model, MCP tools and collections, local tools, memory
scopes, approvals, limits), the registry `version` (bumped on every change synced), whether it is
`active` (a retired agent grants nothing), the manifest's content hash and when it last changed.
The registry is global: records carry no org.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from onb_contracts.envelope import Envelope

__all__ = [
    "AGENT_NAME_PATTERN",
    "AgentCollections",
    "AgentEnvelope",
    "AgentLLM",
    "AgentLimits",
    "AgentMcpGrant",
    "AgentRecord",
    "AgentsEnvelope",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

AGENT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class AgentLLM(BaseModel):
    """An alias from llm.yaml, or a connector and model; `params` are sampling params only."""

    model_config = _FROZEN

    alias: str | None
    connector: str | None
    model: str | None
    params: dict[str, Any]


class AgentCollections(BaseModel):
    model_config = _FROZEN

    read: list[str]
    write: list[str]


class AgentMcpGrant(BaseModel):
    model_config = _FROZEN

    tools: list[str]
    collections: AgentCollections | None


class AgentLimits(BaseModel):
    model_config = _FROZEN

    max_steps: int
    max_tokens: int
    timeout_s: int


class AgentRecord(BaseModel):
    model_config = _FROZEN

    name: str
    version: int
    active: bool
    purpose: str
    llm: AgentLLM
    mcp: dict[str, AgentMcpGrant]
    tools: list[str]
    memory_scopes: list[str]
    requires_approval: list[str]
    limits: AgentLimits
    sha256: str
    updated_at: datetime


class AgentsEnvelope(Envelope[list[AgentRecord]]):
    """Every registered agent, retired ones included, by name."""


class AgentEnvelope(Envelope[AgentRecord]):
    """One registered agent."""
