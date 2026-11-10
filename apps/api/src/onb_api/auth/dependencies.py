"""The request's identity: the token dependency and `require_role` (PRD §7.9 Request context).

`authenticated` reads the bearer token, and the route then runs inside `tenant_scope(org_id)` from
the token, so every repository call is scoped to the caller's org. No token, or a bad one, is 401.
`require_role(...)` adds the role check on top: a listed role passes, any other is 403.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from onb_contracts import ErrorCode
from onb_contracts.auth import Role
from onb_data import tenant_scope

from onb_api.auth.settings import AuthSettings
from onb_api.auth.tokens import InvalidTokenError, Principal, read_token
from onb_api.auth.users import UserStore
from onb_api.errors import AppError

__all__ = ["CurrentPrincipal", "auth_settings", "authenticated", "require_role", "unauthenticated", "user_store"]

# auto_error=False: a missing or non-bearer header comes back as None, and this module answers 401.
_bearer = HTTPBearer(auto_error=False)


def unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


def auth_settings(request: Request) -> AuthSettings:
    settings: AuthSettings = request.app.state.auth
    return settings


def user_store(request: Request) -> UserStore:
    store: UserStore | None = request.app.state.users
    if store is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return store


async def authenticated(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AsyncIterator[Principal]:
    if credentials is None:
        raise unauthenticated()
    try:
        principal = read_token(credentials.credentials, auth_settings(request))
    except InvalidTokenError:
        raise unauthenticated() from None
    with tenant_scope(principal.org_id):
        yield principal


CurrentPrincipal = Annotated[Principal, Depends(authenticated)]


def require_role(*roles: Role) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """A dependency that passes callers with one of `roles` and answers 403 to anyone else."""
    if not roles:
        raise ValueError("require_role needs at least one role")
    allowed = frozenset(roles)

    async def check_role(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise AppError(ErrorCode.FORBIDDEN)
        return principal

    return check_role
