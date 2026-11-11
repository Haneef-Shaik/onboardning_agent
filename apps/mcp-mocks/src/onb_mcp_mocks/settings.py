"""mcp-mocks configuration, from the environment only. The key has no default (PRD §12).

- `MCP_MOCKS_CONTEXT_KEY`: verifies the hub's call contexts for `mcp-mocks`. The hub signs with the
  same key; it is distinct from mcp-mongo's `MCP_CALL_CONTEXT_KEY`, so neither server can mint a
  context the other accepts.
- Optional: `MCP_MOCKS_HOST` (`127.0.0.1`), `MCP_MOCKS_PORT` (8200).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from onb_mcp_mongo.call_context import MIN_KEY_LENGTH

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "Settings", "SettingsError", "load_settings"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8200


class SettingsError(ValueError):
    """A setting is missing or malformed. The message names the variable, never its value."""


@dataclass(frozen=True)
class Settings:
    context_key: str = field(repr=False)
    host: str
    port: int


def _port(env: Mapping[str, str]) -> int:
    raw = env.get("MCP_MOCKS_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise SettingsError("MCP_MOCKS_PORT must be a port number")
    return int(raw)


def load_settings(env: Mapping[str, str]) -> Settings:
    key = env.get("MCP_MOCKS_CONTEXT_KEY", "")
    if not key:
        raise SettingsError("MCP_MOCKS_CONTEXT_KEY is required")
    if len(key) < MIN_KEY_LENGTH:
        raise SettingsError(f"MCP_MOCKS_CONTEXT_KEY must be at least {MIN_KEY_LENGTH} characters")
    return Settings(context_key=key, host=env.get("MCP_MOCKS_HOST") or DEFAULT_HOST, port=_port(env))
