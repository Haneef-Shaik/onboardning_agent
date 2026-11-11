"""mcp-notify configuration, from the environment only. Secrets have no defaults (PRD §12).

- `MCP_NOTIFY_CONTEXT_KEY`: verifies the hub's call contexts for `mcp-notify`. The hub signs with the
  same key; it differs from every other server's key, so no server can mint another's contexts.
- `MCP_NOTIFY_MONGO_URI`: the sink's database user (reads `pii_vault`, `org_keys`, `users`; writes
  `mock_outbox`, `notifications`, `pii_grant_jti`, `pii_access_log`, `audit_log`, the bus collections
  and `telegram_callbacks`).
- `PII_MASTER_KEY`: unwraps the per-org data keys, so the sink can decrypt (PII-1/PII-2).
- `PII_GRANT_VERIFY_KEYS`: the orchestrator's public grant keys (PII-4); never the signing key.
- Optional: `MCP_NOTIFY_DATABASE` (`onboarding`), `PII_SINK_POLICY_FILE`
  (`config/pii_sink_policy.yaml`), `MCP_NOTIFY_HOST` (`127.0.0.1`), `MCP_NOTIFY_PORT` (8210).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from onb_crypto import GrantKeyError, GrantVerifier, MasterKey, MasterKeyError
from onb_mcp_mongo.call_context import MIN_KEY_LENGTH

__all__ = ["DEFAULT_HOST", "DEFAULT_POLICY", "DEFAULT_PORT", "NotifySettings", "SettingsError", "load_notify_settings"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8210
DEFAULT_DATABASE = "onboarding"
DEFAULT_POLICY = Path("config/pii_sink_policy.yaml")


class SettingsError(ValueError):
    """A setting is missing or malformed. The message names the variable, never its value."""


@dataclass(frozen=True)
class NotifySettings:
    context_key: str = field(repr=False)
    mongo_uri: str = field(repr=False)
    master_key: MasterKey = field(repr=False)
    verifier: GrantVerifier = field(repr=False)
    database: str
    policy_file: Path
    host: str
    port: int


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _port(env: Mapping[str, str]) -> int:
    raw = env.get("MCP_NOTIFY_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise SettingsError("MCP_NOTIFY_PORT must be a port number")
    return int(raw)


def load_notify_settings(env: Mapping[str, str]) -> NotifySettings:
    key = _required(env, "MCP_NOTIFY_CONTEXT_KEY")
    if len(key) < MIN_KEY_LENGTH:
        raise SettingsError(f"MCP_NOTIFY_CONTEXT_KEY must be at least {MIN_KEY_LENGTH} characters")
    try:
        master_key = MasterKey.from_env(env)
    except MasterKeyError:
        raise SettingsError("PII_MASTER_KEY is required: base64 of 32 bytes") from None
    try:
        verifier = GrantVerifier.from_env(env)
    except GrantKeyError:
        raise SettingsError("PII_GRANT_VERIFY_KEYS is required: comma-separated base64 public keys") from None
    policy = Path(env.get("PII_SINK_POLICY_FILE") or DEFAULT_POLICY)
    if not policy.is_file():
        raise SettingsError("PII_SINK_POLICY_FILE must name the field → sink policy file")
    return NotifySettings(
        context_key=key,
        mongo_uri=_required(env, "MCP_NOTIFY_MONGO_URI"),
        master_key=master_key,
        verifier=verifier,
        database=env.get("MCP_NOTIFY_DATABASE") or DEFAULT_DATABASE,
        policy_file=policy,
        host=env.get("MCP_NOTIFY_HOST") or DEFAULT_HOST,
        port=_port(env),
    )
