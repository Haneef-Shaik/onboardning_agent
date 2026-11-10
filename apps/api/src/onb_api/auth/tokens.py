"""HS256 access tokens: `{sub, org_id, role, iat, exp}` (PRD §7.8 Auth, §7.9 Identity).

The token is the only place a request's org comes from. `read_token` accepts HS256 only, requires
every claim, and checks each value (a known role, a well-formed org id) before trusting it.
`InvalidTokenError` never carries a claim value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jwt
from onb_contracts.auth import Role, Token
from onb_data import TenantContext

from onb_api.auth.settings import AuthSettings

__all__ = ["ALGORITHM", "MAX_TOKEN_LENGTH", "InvalidTokenError", "Principal", "issue_token", "read_token"]

ALGORITHM = "HS256"
# Real tokens are ~250 characters; refusing big ones bounds the work an anonymous caller can cause.
MAX_TOKEN_LENGTH = 2048
_REQUIRED_CLAIMS = ("sub", "org_id", "role", "iat", "exp")


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired, forged or carries an unusable claim."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling: the token's subject, org and role."""

    user_id: str
    org_id: str
    role: Role


def issue_token(principal: Principal, settings: AuthSettings, *, now: datetime) -> Token:
    expires_at = int((now + settings.token_ttl).timestamp())
    claims = {
        "sub": principal.user_id,
        "org_id": principal.org_id,
        "role": principal.role.value,
        "iat": int(now.timestamp()),
        "exp": expires_at,
    }
    return Token(access_token=jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), expires_at=expires_at)


def read_token(token: str, settings: AuthSettings) -> Principal:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidTokenError("token missing or too long")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError:
        raise InvalidTokenError("token not valid") from None
    return _principal(claims)


def _principal(claims: dict[str, Any]) -> Principal:
    sub, org_id, role = claims["sub"], claims["org_id"], claims["role"]
    if not isinstance(sub, str) or not sub:
        raise InvalidTokenError("token subject not valid")
    try:
        TenantContext(org_id)
        parsed_role = Role(role)
    except ValueError:
        raise InvalidTokenError("token org or role not valid") from None
    return Principal(user_id=sub, org_id=org_id, role=parsed_role)
