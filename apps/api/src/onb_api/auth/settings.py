"""Auth configuration from the environment, checked at startup (PRD §7.8: secret from env).

`AuthSettings.from_env()` raises `ConfigError` when `JWT_SECRET` is missing or too short, so the API
never starts able to sign tokens with an empty or guessable key. Error messages name the variable,
never its value.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

__all__ = ["MIN_SECRET_BYTES", "AuthSettings", "ConfigError", "require_env"]

# RFC 7518 §3.2: an HS256 key must be at least as long as the hash output (256 bits).
MIN_SECRET_BYTES = 32
DEFAULT_TTL_MINUTES = 60
MAX_TTL_MINUTES = 24 * 60
DEFAULT_BCRYPT_ROUNDS = 12
MIN_BCRYPT_ROUNDS = 4
MAX_BCRYPT_ROUNDS = 15


class ConfigError(RuntimeError):
    """A required setting is missing or invalid; the process must not start."""


def require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


@dataclass(frozen=True, slots=True)
class AuthSettings:
    jwt_secret: str = field(repr=False)
    token_ttl: timedelta = timedelta(minutes=DEFAULT_TTL_MINUTES)
    bcrypt_rounds: int = DEFAULT_BCRYPT_ROUNDS

    def __post_init__(self) -> None:
        if len(self.jwt_secret.encode()) < MIN_SECRET_BYTES:
            raise ConfigError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        if not MIN_BCRYPT_ROUNDS <= self.bcrypt_rounds <= MAX_BCRYPT_ROUNDS:
            raise ConfigError(f"bcrypt rounds must be {MIN_BCRYPT_ROUNDS}-{MAX_BCRYPT_ROUNDS}")
        if not timedelta(0) < self.token_ttl <= timedelta(minutes=MAX_TTL_MINUTES):
            raise ConfigError(f"JWT_TTL_MINUTES must be 1-{MAX_TTL_MINUTES}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuthSettings:
        source = os.environ if env is None else env
        secret = require_env(source, "JWT_SECRET")
        return cls(jwt_secret=secret, token_ttl=_ttl(source))

    @classmethod
    def ephemeral(cls) -> AuthSettings:
        """A fresh random secret: for tooling that builds the app but never serves it (OpenAPI export)."""
        return cls(jwt_secret=secrets.token_urlsafe(MIN_SECRET_BYTES))


def _ttl(env: Mapping[str, str]) -> timedelta:
    raw = env.get("JWT_TTL_MINUTES")
    if raw is None:
        return timedelta(minutes=DEFAULT_TTL_MINUTES)
    try:
        minutes = int(raw)
    except ValueError:
        raise ConfigError(f"JWT_TTL_MINUTES must be 1-{MAX_TTL_MINUTES}") from None
    if not 1 <= minutes <= MAX_TTL_MINUTES:
        raise ConfigError(f"JWT_TTL_MINUTES must be 1-{MAX_TTL_MINUTES}")
    return timedelta(minutes=minutes)
