"""Building the agent executor from the environment (RUN-3), for `python -m onb_orchestrator`.

`ORCHESTRATOR_EXECUTOR=agents` makes the worker run every task through its registered agent. Startup
fails fast (`ConfigError`) naming the variable, never its value:

- `CONFIG_DIR`: the config folder (`llm.yaml`, `pii_sink_policy.yaml`); the registry itself is in MongoDB.
- `AGENT_TOKEN_KEY`: signs the per-task agent tokens; the MCP hub verifies them with the same key.
- `MCP_HUB_URL`: the hub's MCP endpoint, the only way an agent reaches a tool.
- `PII_GRANT_SIGNING_KEY`: the orchestrator's Ed25519 key that signs PII grants (PII-4); only this process
  holds it.
- optional `PII_SINK_POLICY_FILE` (default `<CONFIG_DIR>/pii_sink_policy.yaml`) and
  `ORCHESTRATOR_RUN_MAX_TOKENS` (the per-run token budget, PRD §7.1 Budgets).

Model ids and provider settings come from `llm.yaml` and the environment it reads, never from code. The
default executor stays the stub (`ORCHESTRATOR_EXECUTOR` unset), so no model is called unless chosen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from onb_agents.registry import AgentRegistry
from onb_agents.registry.mongo import MongoRegistryStore
from onb_agents.runtime import HubToolSource
from onb_agents.tokens import MIN_KEY_LENGTH
from onb_crypto import GrantIssuer, GrantKeyError, GrantSigningKey, SinkPolicyError, load_sink_policy
from onb_data import AuditLog, DataStore
from onb_llm import LLMConfigError, LLMRuntime, discover_connectors, load_llm_config

from .agent_executor import AgentTaskExecutor, RuntimeModelSource

__all__ = ["DEFAULT_RUN_MAX_TOKENS", "AgentSettings", "ConfigError", "build_agent_executor"]

DEFAULT_RUN_MAX_TOKENS: Final = 200_000
POLICY_FILE: Final = "pii_sink_policy.yaml"


class ConfigError(Exception):
    """A required setting is missing or malformed. Names the variable, never its value."""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    config_dir: Path
    token_key: str = field(repr=False)
    hub_url: str
    grant_key: str = field(repr=False)
    policy_file: Path
    run_max_tokens: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentSettings:
        config_dir = Path(_required(env, "CONFIG_DIR"))
        token_key = _required(env, "AGENT_TOKEN_KEY")
        if len(token_key) < MIN_KEY_LENGTH:
            raise ConfigError(f"AGENT_TOKEN_KEY must be at least {MIN_KEY_LENGTH} characters")
        hub_url = _required(env, "MCP_HUB_URL")
        parts = urlsplit(hub_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ConfigError("MCP_HUB_URL must be an http(s) URL")
        grant_key = _required(env, "PII_GRANT_SIGNING_KEY")
        try:
            GrantSigningKey.from_encoded(grant_key)
        except GrantKeyError:
            raise ConfigError("PII_GRANT_SIGNING_KEY must be base64 of a 32-byte Ed25519 seed") from None
        policy_file = Path(env.get("PII_SINK_POLICY_FILE") or config_dir / POLICY_FILE)
        if not policy_file.is_file():
            raise ConfigError("PII_SINK_POLICY_FILE must name the field → sink policy file")
        return cls(config_dir, token_key, hub_url, grant_key, policy_file, _max_tokens(env))


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise ConfigError(f"missing required setting: {name}")
    return value


def _max_tokens(env: Mapping[str, str]) -> int:
    raw = env.get("ORCHESTRATOR_RUN_MAX_TOKENS")
    if raw is None or not raw.strip():
        return DEFAULT_RUN_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("ORCHESTRATOR_RUN_MAX_TOKENS must be a positive whole number of tokens") from None
    if value < 1:
        raise ConfigError("ORCHESTRATOR_RUN_MAX_TOKENS must be a positive whole number of tokens")
    return value


def build_agent_executor(store: DataStore, settings: AgentSettings, env: Mapping[str, str]) -> AgentTaskExecutor:
    """The executor over MongoDB's registry, LLM-1's runtime, the MCP hub and the grant issuer. Raises
    `ConfigError` when `llm.yaml` or the sink policy cannot be loaded (names the file, not its contents)."""
    try:
        runtime = LLMRuntime.start(load_llm_config(settings.config_dir / "llm.yaml", env), discover_connectors(), env)
    except LLMConfigError as error:
        raise ConfigError(f"llm.yaml / the LLM environment is not usable: {type(error).__name__}") from None
    try:
        policy = load_sink_policy(settings.policy_file)
    except SinkPolicyError:
        raise ConfigError("PII_SINK_POLICY_FILE is not a valid field → sink policy") from None
    audit = AuditLog(store)
    return AgentTaskExecutor(
        store=store,
        agents=AgentRegistry(MongoRegistryStore(store), audit),
        models=RuntimeModelSource(runtime),
        tools=HubToolSource(settings.hub_url),
        auditor=audit,
        token_key=settings.token_key,
        grants=GrantIssuer(GrantSigningKey.from_encoded(settings.grant_key), policy),
        run_max_tokens=settings.run_max_tokens,
    )
