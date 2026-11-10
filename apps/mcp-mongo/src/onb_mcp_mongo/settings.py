"""mcp-mongo configuration, from the environment only. Secrets have no defaults (PRD §12)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .call_context import MIN_KEY_LENGTH

__all__ = ["Settings", "SettingsError", "load_settings"]

DEFAULT_DATABASE = "onboarding"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class SettingsError(ValueError):
    """A setting is missing or malformed. The message names the variable, never its value."""


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = field(repr=False)
    database: str
    context_key: str = field(repr=False)
    host: str
    port: int


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _port(env: Mapping[str, str]) -> int:
    raw = env.get("MCP_MONGO_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise SettingsError("MCP_MONGO_PORT must be a port number")
    return int(raw)


def load_settings(env: Mapping[str, str]) -> Settings:
    key = _required(env, "MCP_CALL_CONTEXT_KEY")
    if len(key) < MIN_KEY_LENGTH:
        raise SettingsError(f"MCP_CALL_CONTEXT_KEY must be at least {MIN_KEY_LENGTH} characters")
    return Settings(
        mongo_uri=_required(env, "MCP_MONGO_URI"),
        database=env.get("MCP_MONGO_DATABASE") or DEFAULT_DATABASE,
        context_key=key,
        host=env.get("MCP_MONGO_HOST") or DEFAULT_HOST,
        port=_port(env),
    )
