"""The call context the MCP hub signs for every call it forwards to mcp-mongo (PRD §7.4, §7.5).

The hub verifies the agent token and the manifest, then sends one short-lived HS256 JWS in the
`X-Onb-Call-Context` header: `org_id`, `agent`, `run_id` and the agent's read and write
collections. It is the only source of tenant scope here; tool arguments never carry it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import jwt

__all__ = [
    "AUDIENCE",
    "HEADER",
    "ISSUER",
    "MAX_LIFETIME_S",
    "MIN_KEY_LENGTH",
    "CallContext",
    "CallContextError",
    "sign_call_context",
    "verify_call_context",
]

HEADER = "x-onb-call-context"
ISSUER = "mcp-hub"
AUDIENCE = "mcp-mongo"
ALGORITHM = "HS256"
# One forwarded call, not a session: a context lives at most five minutes.
MAX_LIFETIME_S = 300
DEFAULT_LIFETIME_S = 60
MIN_KEY_LENGTH = 32

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_AGENT = re.compile(r"[A-Za-z0-9_.-]{1,128}")
COLLECTION_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
_COLLECTION = re.compile(COLLECTION_PATTERN)
_REQUIRED = ["exp", "iat", "iss", "aud", "org_id", "agent", "run_id", "collections"]


class CallContextError(Exception):
    """The call context is missing, forged, expired or malformed. The message never holds the token."""


@dataclass(frozen=True, slots=True)
class CallContext:
    org_id: str
    agent: str
    run_id: str
    read: frozenset[str]
    write: frozenset[str]

    @property
    def readable(self) -> frozenset[str]:
        """An agent may read what it may write."""
        return self.read | self.write


def _check_key(key: str) -> None:
    if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
        raise ValueError(f"the call-context key must be at least {MIN_KEY_LENGTH} characters")


def _collections(value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(n, str) and _COLLECTION.fullmatch(n) for n in value):
        raise CallContextError("call context collections must be lists of plain collection names")
    return frozenset(value)


def _text(claims: Mapping[str, Any], name: str, pattern: re.Pattern[str]) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CallContextError(f"call context claim {name} is malformed")
    return value


def _from_claims(claims: Mapping[str, Any]) -> CallContext:
    if claims["exp"] - claims["iat"] > MAX_LIFETIME_S:
        raise CallContextError(f"call context lives longer than {MAX_LIFETIME_S} seconds")
    collections = claims["collections"]
    if not isinstance(collections, Mapping):
        raise CallContextError("call context collections must be an object with read and write lists")
    return CallContext(
        org_id=_text(claims, "org_id", _ID),
        agent=_text(claims, "agent", _AGENT),
        run_id=_text(claims, "run_id", _ID),
        read=_collections(collections.get("read")),
        write=_collections(collections.get("write")),
    )


def verify_call_context(token: str | None, key: str) -> CallContext:
    """The context in `token`, or `CallContextError`. Only HS256 with `key` is accepted."""
    _check_key(key)
    if not token:
        raise CallContextError("no call context: mcp-mongo is reached only through the MCP hub")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": _REQUIRED},
        )
    except jwt.PyJWTError as exc:
        raise CallContextError(f"call context refused: {type(exc).__name__}") from None
    return _from_claims(claims)


def _sorted(names: Iterable[str]) -> list[str]:
    return sorted(names)


def sign_call_context(ctx: CallContext, key: str, *, lifetime_s: int = DEFAULT_LIFETIME_S) -> str:
    """The header value for `ctx`. Used by the hub (HUB-1) and by tests."""
    _check_key(key)
    if not 0 < lifetime_s <= MAX_LIFETIME_S:
        raise ValueError(f"lifetime must be 1..{MAX_LIFETIME_S} seconds")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + lifetime_s,
        "org_id": ctx.org_id,
        "agent": ctx.agent,
        "run_id": ctx.run_id,
        "collections": {"read": _sorted(ctx.read), "write": _sorted(ctx.write)},
    }
    return jwt.encode(claims, key, algorithm=ALGORITHM)
