"""mcp-hub configuration, from the environment only. Secrets have no defaults (PRD §12).

- `AGENT_TOKEN_KEY`: verifies agent tokens; the orchestrator signs with the same key.
- `MCP_CALL_CONTEXT_KEY`: signs call contexts; mcp-mongo verifies with the same key.
- `MCP_HUB_MONGO_URI`: the hub's own database user (reads `agent_registry`, appends `audit_log`).
- `MCP_HUB_MONGO_SERVER_URL`: mcp-mongo's streamable-HTTP endpoint.
- Optional: `MCP_HUB_DATABASE` (`onboarding`), `MCP_HUB_HOST` (`127.0.0.1`), `MCP_HUB_PORT` (8100),
  `MCP_HUB_ALLOWED_HOSTS` (comma-separated `Host` names the hub answers).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from onb_agents.tokens import MIN_KEY_LENGTH

from .catalog import MONGO_SERVER
from .server import DEFAULT_ALLOWED_HOSTS

__all__ = ["Settings", "SettingsError", "load_settings"]

DEFAULT_DATABASE = "onboarding"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100


class SettingsError(ValueError):
    """A setting is missing or malformed. The message names the variable, never its value."""


@dataclass(frozen=True)
class Settings:
    token_key: str = field(repr=False)
    context_key: str = field(repr=False)
    mongo_uri: str = field(repr=False)
    downstream: Mapping[str, str]
    database: str
    host: str
    port: int
    allowed_hosts: tuple[str, ...]


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _key(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if len(value) < MIN_KEY_LENGTH:
        raise SettingsError(f"{name} must be at least {MIN_KEY_LENGTH} characters")
    return value


def _url(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if not value.startswith(("http://", "https://")):
        raise SettingsError(f"{name} must be an http(s) URL")
    return value


def _port(env: Mapping[str, str]) -> int:
    raw = env.get("MCP_HUB_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise SettingsError("MCP_HUB_PORT must be a port number")
    return int(raw)


def _hosts(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("MCP_HUB_ALLOWED_HOSTS", "")
    hosts = tuple(h.strip() for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_ALLOWED_HOSTS


def load_settings(env: Mapping[str, str]) -> Settings:
    token_key = _key(env, "AGENT_TOKEN_KEY")
    context_key = _key(env, "MCP_CALL_CONTEXT_KEY")
    if token_key == context_key:
        raise SettingsError("AGENT_TOKEN_KEY and MCP_CALL_CONTEXT_KEY must differ")
    return Settings(
        token_key=token_key,
        context_key=context_key,
        mongo_uri=_required(env, "MCP_HUB_MONGO_URI"),
        downstream={MONGO_SERVER: _url(env, "MCP_HUB_MONGO_SERVER_URL")},
        database=env.get("MCP_HUB_DATABASE") or DEFAULT_DATABASE,
        host=env.get("MCP_HUB_HOST") or DEFAULT_HOST,
        port=_port(env),
        allowed_hosts=_hosts(env),
    )
