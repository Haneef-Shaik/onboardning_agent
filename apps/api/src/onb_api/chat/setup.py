"""Builds the chat service for `onb_api.main` (CHAT-1).

The assistant needs the same two settings the orchestrator's agents do: `AGENT_TOKEN_KEY` (signs the
per-task hub token; the hub verifies it with the same key) and `MCP_HUB_URL` (the only way an agent reaches
a tool). When either is unset the chat is not built and its routes answer 503, so an API that runs without
the hub still serves everything else; a key that is set but too short fails the start.
"""

from __future__ import annotations

from collections.abc import Mapping

from onb_agents.registry import AgentRegistry
from onb_agents.runtime import AgentRunner, HubToolSource, runtime_models
from onb_data import AuditLog, DataStore
from onb_llm import LLMConfig, LLMRuntime, discover_connectors

from onb_api.chat.agent import AgentRunAssistant
from onb_api.chat.service import ChatService
from onb_api.chat.store import ChatStore
from onb_api.runs import RunService

__all__ = ["MIN_KEY_LENGTH", "build_chat"]

MIN_KEY_LENGTH = 32


def build_chat(
    env: Mapping[str, str],
    *,
    store: DataStore,
    runs: RunService,
    registry: AgentRegistry,
    audit_log: AuditLog,
    llm_config: LLMConfig,
) -> ChatService | None:
    token_key = env.get("AGENT_TOKEN_KEY", "")
    hub_url = env.get("MCP_HUB_URL", "")
    if not token_key or not hub_url or runs.control is None:
        return None
    if len(token_key) < MIN_KEY_LENGTH:
        raise ValueError(f"AGENT_TOKEN_KEY must be at least {MIN_KEY_LENGTH} characters")
    if not hub_url.startswith(("http://", "https://")):
        raise ValueError("MCP_HUB_URL must be an http(s) URL")
    runtime = LLMRuntime.start(llm_config, discover_connectors(), env)
    runner = AgentRunner(
        models=runtime_models(runtime), tools=HubToolSource(hub_url), auditor=audit_log, token_key=token_key
    )
    return ChatService(
        store=ChatStore(store),
        control=runs.control,
        assistant=AgentRunAssistant(runner=runner, agents=registry),
    )
