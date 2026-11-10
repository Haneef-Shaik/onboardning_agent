"""Each role x each guarded route gives the expected 200/403 (AUTH-1 box 3, PRD §3 roles).

`ROLE_MATRIX` is the written-down expectation, independent of the code. The completeness tests fail
when a route is added without a row here, or without the token dependency, so every later route
joins the matrix. `require_role` itself is exercised on probe routes with every role combination.
"""

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.auth.dependencies import authenticated, require_role
from onb_api.auth.tokens import Principal
from onb_api.responses import respond
from onb_api.testing import bearer, token_for
from onb_contracts import Envelope
from onb_contracts.auth import Role

# One acme user per role (seed_v1 ids).
ACTORS = {
    Role.HR_ADMIN: ("u_acme_hradmin", "org_acme"),
    Role.HR: ("u_acme_hr1", "org_acme"),
    Role.MANAGER: ("u_acme_mgr_ravi", "org_acme"),
}
ALL_200 = {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 200}

# Routes anyone may call without a token.
PUBLIC_ROUTES = {("GET", "/health"), ("POST", "/auth/login")}

# Every guarded route: (method, path) -> {role: expected status}.
ROLE_MATRIX: dict[tuple[str, str], dict[Role, int]] = {
    ("GET", "/me"): ALL_200,
    # AUD-1: the audit log is HR_ADMIN only (PRD §3, §10 Registry & audit).
    ("GET", "/audit"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    # REG-1: the agent registry is HR_ADMIN only (PRD U8, §10 Registry & audit).
    ("GET", "/agents"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    ("GET", "/agents/{name}"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
}

# A concrete path for each route with path parameters: an existing record, so HR_ADMIN gets 200.
SAMPLE_PATHS = {"/agents/{name}": "/agents/db_agent"}


def _routes(app: FastAPI) -> list[APIRoute]:
    return [c.original_route for c in iter_route_contexts(app.routes) if isinstance(c.original_route, APIRoute)]


def _keys(route: APIRoute) -> Iterator[tuple[str, str]]:
    for method in sorted(route.methods or ()):
        yield method, route.path


def _depends_on_authentication(route: APIRoute) -> bool:
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call is authenticated:
            return True
        pending.extend(dependant.dependencies)
    return False


def test_every_route_is_public_or_in_the_role_matrix() -> None:
    keys = {key for route in _routes(create_app()) for key in _keys(route)}
    assert keys - PUBLIC_ROUTES == set(ROLE_MATRIX), "add the new route to ROLE_MATRIX (or PUBLIC_ROUTES)"


def test_every_guarded_route_takes_the_org_from_the_token() -> None:
    unguarded = [
        key
        for route in _routes(create_app())
        for key in _keys(route)
        if key not in PUBLIC_ROUTES and not _depends_on_authentication(route)
    ]
    assert unguarded == []


def test_the_matrix_covers_every_role() -> None:
    assert all(set(expected) == set(Role) for expected in ROLE_MATRIX.values())


@pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
@pytest.mark.parametrize("route", sorted(ROLE_MATRIX), ids=lambda k: f"{k[0]} {k[1]}")
def test_role_matrix(client: TestClient, route: tuple[str, str], role: Role) -> None:
    method, path = route
    user_id, org_id = ACTORS[role]
    response = client.request(method, SAMPLE_PATHS.get(path, path), headers=bearer(token_for(user_id, org_id, role)))
    assert response.status_code == ROLE_MATRIX[route][role], response.text


@pytest.mark.parametrize("route", sorted(ROLE_MATRIX), ids=lambda k: f"{k[0]} {k[1]}")
def test_every_guarded_route_refuses_a_missing_token(client: TestClient, route: tuple[str, str]) -> None:
    method, path = route
    assert client.request(method, SAMPLE_PATHS.get(path, path)).status_code == 401


def test_every_path_parameter_route_has_a_sample_path() -> None:
    assert {path for _, path in ROLE_MATRIX if "{" in path} == set(SAMPLE_PATHS)


# --- require_role on probe routes ----------------------------------------------------------------

PROBES: dict[str, tuple[Role, ...]] = {
    "/probe/admin": (Role.HR_ADMIN,),
    "/probe/hr": (Role.HR_ADMIN, Role.HR),
    "/probe/manager": (Role.MANAGER,),
    "/probe/anyone": (Role.HR_ADMIN, Role.HR, Role.MANAGER),
}


@pytest.fixture
def probed(app: FastAPI, client: TestClient) -> TestClient:
    for path, roles in PROBES.items():

        async def probe(principal: Annotated[Principal, Depends(require_role(*roles))]) -> Envelope[str]:
            return respond(principal.role.value)

        app.add_api_route(path, probe, include_in_schema=False)
    return client


@pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
@pytest.mark.parametrize("path", sorted(PROBES))
def test_require_role_gives_200_to_listed_roles_and_403_to_others(probed: TestClient, path: str, role: Role) -> None:
    user_id, org_id = ACTORS[role]
    response = probed.get(path, headers=bearer(token_for(user_id, org_id, role)))
    expected = 200 if role in PROBES[path] else 403
    assert response.status_code == expected
    if expected == 403:
        assert response.json()["error"]["code"] == "FORBIDDEN"


def test_require_role_checks_the_token_before_the_role(probed: TestClient) -> None:
    assert probed.get("/probe/admin").status_code == 401


def test_require_role_needs_at_least_one_role() -> None:
    with pytest.raises(ValueError, match="role"):
        require_role()
