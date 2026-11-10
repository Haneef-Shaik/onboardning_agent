"""`POST /auth/login` and `GET /me` (PRD §10 Auth).

Login finds the user's org in the login directory, reads the user inside that org's scope and checks
the bcrypt hash. An unknown email, a missing record and a wrong password all give the same 401, and
an unknown email still costs one bcrypt check. No refresh tokens (PRD §7.8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from onb_contracts import Envelope
from onb_contracts.auth import LoginRequest, Me, MeEnvelope, Token, TokenEnvelope
from onb_data import tenant_scope

from onb_api.auth.dependencies import CurrentPrincipal, auth_settings, unauthenticated, user_store
from onb_api.auth.passwords import verify_password
from onb_api.auth.settings import AuthSettings
from onb_api.auth.tokens import Principal, issue_token
from onb_api.auth.users import UserRecord, UserStore
from onb_api.responses import respond

__all__ = ["router"]

router = APIRouter(tags=["auth"])


async def _user_for_login(users: UserStore, email: str) -> UserRecord | None:
    entry = await users.find_login(email)
    if entry is None:
        return None
    with tenant_scope(entry.org_id):
        return await users.get_user(entry.user_id)


@router.post("/auth/login", response_model=TokenEnvelope, operation_id="login")
async def login(
    body: LoginRequest,
    settings: Annotated[AuthSettings, Depends(auth_settings)],
    users: Annotated[UserStore, Depends(user_store)],
) -> Envelope[Token]:
    user = await _user_for_login(users, body.email)
    password_ok = verify_password(body.password, user.password_hash if user is not None else None)
    if user is None or not password_ok:
        raise unauthenticated()
    principal = Principal(user_id=user.user_id, org_id=user.org_id, role=user.role)
    return respond(issue_token(principal, settings, now=datetime.now(UTC)))


@router.get("/me", response_model=MeEnvelope, operation_id="getMe")
async def me(principal: CurrentPrincipal, users: Annotated[UserStore, Depends(user_store)]) -> Envelope[Me]:
    user = await users.get_user(principal.user_id)
    if user is None:
        raise unauthenticated()
    return respond(Me(user_id=user.user_id, org_id=user.org_id, role=principal.role, name=user.name, email=user.email))
