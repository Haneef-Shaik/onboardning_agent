"""The short-lived agent token (PRD §7.4, §7.9 "agent tokens are bound to one org and one run").

The orchestrator issues one per task with `issue_agent_token(...)`; the MCP hub verifies it with
`verify_agent_token(...)`. It is an HS256 JWS (`iss=orchestrator`, `aud=mcp-hub`) carrying `agent`,
`run_id`, `org_id`, `scopes`, `iat`, `exp` and `jti`, and it lives at most 15 minutes.

`scopes` narrow what this task may do inside the agent's manifest: hub tool names (`mongo__find`)
and approvals granted by a human for this task (`approved:update_one:new_hires`). The hub allows a
call only when both the manifest and the scopes allow it.

The agent side gets `hub_client_config(url, token)`: the hub's URL and this token, nothing else. It
never holds a downstream credential (PRD §5.2: the hub injects them).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import jwt

__all__ = [
    "AUDIENCE",
    "ISSUER",
    "MAX_LIFETIME_S",
    "MIN_KEY_LENGTH",
    "AgentClaims",
    "AgentTokenError",
    "VerifiedAgentToken",
    "hub_client_config",
    "issue_agent_token",
    "verify_agent_token",
]

ISSUER = "orchestrator"
AUDIENCE = "mcp-hub"
ALGORITHM = "HS256"
MAX_LIFETIME_S = 15 * 60
DEFAULT_LIFETIME_S = 10 * 60
LEEWAY_S = 5
MIN_KEY_LENGTH = 32
MAX_SCOPES = 64

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_AGENT = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SCOPE = re.compile(r"[a-z][a-z0-9_:.-]{0,127}")
_JTI = re.compile(r"[0-9a-f]{32}")
_REQUIRED = ["iss", "aud", "iat", "exp", "jti", "agent", "run_id", "org_id", "scopes"]

Reason = Literal["no_token", "token_invalid", "token_expired"]


class AgentTokenError(Exception):
    """The token is missing, forged, malformed or expired. The message never holds the token.

    `claims` is set only for an expired token whose signature verified: its org is genuine, so the
    hub can audit the denial in that org. A forged or malformed token gives no claims.
    """

    def __init__(self, reason: Reason, message: str, claims: AgentClaims | None = None) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.claims = claims


def _check(value: object, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"agent token {what} is malformed")
    return value


@dataclass(frozen=True, slots=True)
class AgentClaims:
    agent: str
    run_id: str
    org_id: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        _check(self.agent, _AGENT, "agent")
        _check(self.run_id, _ID, "run_id")
        _check(self.org_id, _ID, "org_id")
        if not isinstance(self.scopes, tuple) or len(self.scopes) > MAX_SCOPES:
            raise ValueError(f"agent token scopes must be a tuple of at most {MAX_SCOPES} entries")
        for scope in self.scopes:
            _check(scope, _SCOPE, "scope")


@dataclass(frozen=True, slots=True)
class VerifiedAgentToken:
    claims: AgentClaims
    jti: str
    issued_at: int
    expires_at: int


def _check_key(key: str) -> None:
    if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
        raise ValueError(f"the agent-token key must be at least {MIN_KEY_LENGTH} characters")


def issue_agent_token(
    claims: AgentClaims, key: str, *, lifetime_s: int = DEFAULT_LIFETIME_S, now: int | None = None
) -> str:
    """A signed token for one task of one run. `now` is for tests."""
    _check_key(key)
    if not 0 < lifetime_s <= MAX_LIFETIME_S:
        raise ValueError(f"agent token lifetime must be 1..{MAX_LIFETIME_S} seconds")
    issued = int(time.time()) if now is None else now
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": issued,
        "exp": issued + lifetime_s,
        "jti": uuid.uuid4().hex,
        "agent": claims.agent,
        "run_id": claims.run_id,
        "org_id": claims.org_id,
        "scopes": list(claims.scopes),
    }
    return jwt.encode(payload, key, algorithm=ALGORITHM)


def _claims(payload: Mapping[str, Any]) -> AgentClaims:
    scopes = payload["scopes"]
    if not isinstance(scopes, list):
        raise ValueError("agent token scopes must be a list")
    return AgentClaims(
        agent=payload["agent"], run_id=payload["run_id"], org_id=payload["org_id"], scopes=tuple(scopes)
    )


def _decode(token: str, key: str, *, verify_exp: bool) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        token,
        key,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        leeway=LEEWAY_S,
        options={"require": _REQUIRED, "verify_exp": verify_exp},
    )
    return decoded


def _verified(payload: Mapping[str, Any]) -> VerifiedAgentToken:
    issued, expires = payload["iat"], payload["exp"]
    if not isinstance(issued, int) or not isinstance(expires, int) or not 0 < expires - issued <= MAX_LIFETIME_S:
        raise ValueError(f"agent token must live 1..{MAX_LIFETIME_S} seconds")
    jti = _check(payload["jti"], _JTI, "jti")
    return VerifiedAgentToken(claims=_claims(payload), jti=jti, issued_at=issued, expires_at=expires)


def verify_agent_token(token: str | None, key: str) -> VerifiedAgentToken:
    """The verified token, or `AgentTokenError`. Only HS256 with `key` is accepted."""
    _check_key(key)
    if not token:
        raise AgentTokenError("no_token", "no agent token: call the hub with `Authorization: Bearer <token>`")
    try:
        return _verified(_decode(token, key, verify_exp=True))
    except jwt.ExpiredSignatureError:
        expired = _expired_claims(token, key)
        raise AgentTokenError("token_expired", "the agent token has expired", expired) from None
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AgentTokenError("token_invalid", f"agent token refused: {type(exc).__name__}") from None


def _expired_claims(token: str, key: str) -> AgentClaims | None:
    """The claims of an expired token whose signature and shape verify; None otherwise."""
    try:
        return _verified(_decode(token, key, verify_exp=False)).claims
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def hub_client_config(url: str, token: str) -> dict[str, Any]:
    """Everything the agent side needs to reach its tools: the hub's URL and its own agent token."""
    return {"url": url, "headers": {"Authorization": f"Bearer {token}"}}
