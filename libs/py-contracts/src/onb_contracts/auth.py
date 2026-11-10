"""Login and identity contracts (PRD §3 Users & roles, §7.8 Auth, §10 `POST /auth/login`, `GET /me`).

The login body names no org: the org comes from the user record, then lives only in the signed
token. Responses may show the org id; requests never carry it (PRD §7.9 Identity).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from onb_contracts.envelope import Envelope
from onb_contracts.inputs import RequestModel

# RFC 5321 caps an address at 254 characters; 320 leaves room and still bounds the work.
MAX_EMAIL_LENGTH = 320
# bcrypt reads at most 72 bytes; the API refuses longer passwords when it hashes them.
MAX_PASSWORD_LENGTH = 256
# The OAuth 2 token type (RFC 6750); the scheme name, not a credential.
BEARER: Literal["bearer"] = "bearer"


class Role(StrEnum):
    HR_ADMIN = "HR_ADMIN"
    HR = "HR"
    MANAGER = "MANAGER"


class LoginRequest(RequestModel):
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class Token(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = Field(default=BEARER)
    expires_at: int = Field(description="Unix time (seconds) when the token stops being accepted")


class Me(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    org_id: str
    role: Role
    name: str
    email: str


class TokenEnvelope(Envelope[Token]):
    """A signed access token."""


class MeEnvelope(Envelope[Me]):
    """The signed-in user."""
