"""Every API route: an org-A actor cannot read, list, update or infer org-B data (PRD §7.9 Testing).

`ROUTE_CASES` has one row per (method, path) that `create_app()` serves; `test_xt_coverage.py`
fails the run when a route has no row. Each row gives the ordinary request an org-A actor makes
(`request`) and the route's own cross-tenant checks (`check`). Every row also gets the generic checks
below, for each org-A role:

* the response carries none of org B's seeded strings (`assert_no_leak`);
* naming org B in a query parameter or header changes nothing, and in a body is ignored or refused.

A route that takes an id must also show that org B's id answers exactly like an id that does not
exist (`assert_indistinguishable`: 404 either way, never 403); a route that creates something unique
must show that a value taken in org B does not collide (no unique-index inference).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient
from onb_api.auth.tokens import read_token
from onb_api.testing import TEST_SETTINGS, bearer, token_for
from onb_contracts.auth import Role

from .conftest import World
from .world import ORG_A, ORG_B, Seen, assert_indistinguishable, assert_no_leak, audit_rows, email_of, without_volatile

Request = dict[str, Any]  # keyword arguments for `TestClient.request`

# One org-A actor per role (seed_v1 ids).
ACTORS: Mapping[Role, str] = {Role.HR_ADMIN: "u_acme_hradmin", Role.HR: "u_acme_hr1", Role.MANAGER: "u_acme_mgr_ravi"}
# An org-B HR_ADMIN: seed_v1 has none, and /audit does not need a stored user.
B_ADMIN = ("u_globex_xt_admin", ORG_B, Role.HR_ADMIN)

# Ways a caller might try to name another org. None of them may change what the route does.
ORG_PARAMS = ("org_id", "orgId", "org", "tenant", "tenant_id")
ORG_HEADERS = ("X-Org-Id", "X-Tenant-Id", "X-Org")


class Response(Protocol):
    """What the suite reads of a test-client response."""

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...


def _token(role: Role) -> str:
    return token_for(ACTORS[role], ORG_A, role)


def seen(response: Response, *, volatile: frozenset[str] = frozenset()) -> Seen:
    body = response.json()
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = {**body, "data": {k: v for k, v in body["data"].items() if k not in volatile}}
    return Seen(response.status_code, without_volatile(body))


@dataclass(frozen=True)
class RouteCase:
    request: Callable[[World, Role], Request]
    """The ordinary request an org-A actor with this role makes."""
    check: Callable[[TestClient, World], None]
    """The route's own cross-tenant assertions."""
    volatile: frozenset[str] = field(default=frozenset())
    """Fields of `data` that differ on every call by design (a fresh token)."""


# --- GET /health -----------------------------------------------------------------------------------


def _health_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/health", "headers": bearer(_token(role))}


def _health_check(client: TestClient, _: World) -> None:
    """Nothing about any org: the same answer to org A, org B and no token at all."""
    as_a = seen(client.get("/health", headers=bearer(_token(Role.HR_ADMIN))))
    as_b = seen(client.get("/health", headers=bearer(token_for(*B_ADMIN))))
    anonymous = seen(client.get("/health"))
    assert as_a == as_b == anonymous


# --- POST /auth/login --------------------------------------------------------------------------------


def _login_request(xt: World, role: Role) -> Request:
    return {"method": "POST", "url": "/auth/login", "json": {"email": email_of(ACTORS[role]), "password": xt.password}}


def _login_check(client: TestClient, xt: World) -> None:
    # Logging in as an org-A user yields an org-A token, whatever org the body names.
    for body in (_login_request(xt, Role.HR)["json"], {**_login_request(xt, Role.HR)["json"], "org_id": ORG_B}):
        response = client.post("/auth/login", json=body)
        if response.status_code == 200:
            assert read_token(response.json()["data"]["access_token"], TEST_SETTINGS).org_id == ORG_A
        else:
            assert response.status_code == 422, response.text
    # An org-B email with a wrong password answers exactly like an email nobody has.
    foreign = client.post("/auth/login", json={"email": email_of("u_globex_hr1"), "password": "not-the-password"})
    missing = client.post("/auth/login", json={"email": email_of("u_nobody_xt"), "password": "not-the-password"})
    assert_indistinguishable(seen(foreign), seen(missing), "POST /auth/login with an org-B email")


# --- GET /me -------------------------------------------------------------------------------------------


def _me_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/me", "headers": bearer(_token(role))}


def _me_check(client: TestClient, _: World) -> None:
    for role, user_id in ACTORS.items():
        data = client.get("/me", headers=bearer(_token(role))).json()["data"]
        assert (data["user_id"], data["org_id"]) == (user_id, ORG_A)
    # A valid org-A token naming an org-B user finds nobody, exactly like a user that does not exist.
    foreign = client.get("/me", headers=bearer(token_for("u_globex_hr1", ORG_A, Role.HR)))
    missing = client.get("/me", headers=bearer(token_for("u_nobody_xt", ORG_A, Role.HR)))
    assert foreign.status_code == 401
    assert_indistinguishable(seen(foreign), seen(missing), "GET /me for an org-B user id")


# --- GET /audit ----------------------------------------------------------------------------------------


def _audit_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/audit", "headers": bearer(_token(role)), "params": {"limit": 100}}


def _audit(client: TestClient, params: Mapping[str, Any], token: str | None = None) -> Response:
    return client.get("/audit", params=params, headers=bearer(token or _token(Role.HR_ADMIN)))


def _audit_check(client: TestClient, _: World) -> None:
    body = _audit(client, {"limit": 100}).json()
    assert {r["org_id"] for r in body["data"]} == {ORG_A}
    assert len(body["data"]) == len(audit_rows(ORG_A))
    # Filtering on org B's actors, actions or targets finds exactly what a value nobody has finds.
    for key, foreign_value, missing_value in [
        ("actor_id", "u_globex_hr1", "u_nobody_xt"),
        ("actor_id", "globex_doc_agent", "nobody_xt_agent"),
        ("action", "xt.globex_only", "xt.nobody_has_this"),
        ("target", "hire:h_globex_001/pan", "hire:h_nobody_xt/pan"),
    ]:
        foreign, missing = _audit(client, {key: foreign_value}), _audit(client, {key: missing_value})
        assert foreign.json()["data"] == []
        assert_indistinguishable(seen(foreign), seen(missing), f"GET /audit?{key}=<org B's>")
    # Paging past org A's records finds nothing, not org B's.
    assert _audit(client, {"page": 2, "limit": len(audit_rows(ORG_A))}).json()["data"] == []
    # And org B's admin, symmetrically, sees only org B.
    b_body = _audit(client, {"limit": 100}, token_for(*B_ADMIN)).json()
    assert {r["org_id"] for r in b_body["data"]} == {ORG_B}


ROUTE_CASES: Mapping[tuple[str, str], RouteCase] = {
    ("GET", "/health"): RouteCase(_health_request, _health_check),
    ("POST", "/auth/login"): RouteCase(
        _login_request, _login_check, volatile=frozenset({"access_token", "expires_at"})
    ),
    ("GET", "/me"): RouteCase(_me_request, _me_check),
    ("GET", "/audit"): RouteCase(_audit_request, _audit_check),
}

_ROUTE_IDS = [f"{method} {path}" for method, path in ROUTE_CASES]


@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_the_routes_own_cross_tenant_checks(api: TestClient, xt_world: World, route: tuple[str, str]) -> None:
    ROUTE_CASES[route].check(api, xt_world)


@pytest.mark.parametrize("role", list(ACTORS), ids=lambda r: r.value)
@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_no_org_b_data_reaches_an_org_a_actor(
    api: TestClient, xt_world: World, route: tuple[str, str], role: Role
) -> None:
    response = api.request(**ROUTE_CASES[route].request(xt_world, role))
    assert response.status_code < 500, response.text
    assert_no_leak(response.text, f"{route} as {role.value}")
    assert_no_leak(repr(sorted(response.headers.items())), f"{route} headers as {role.value}")


@pytest.mark.parametrize("role", list(ACTORS), ids=lambda r: r.value)
@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_naming_org_b_in_the_request_changes_nothing(
    api: TestClient, xt_world: World, route: tuple[str, str], role: Role
) -> None:
    case = ROUTE_CASES[route]
    request = case.request(xt_world, role)
    baseline = seen(api.request(**request), volatile=case.volatile)
    params = {**request.get("params", {}), **dict.fromkeys(ORG_PARAMS, ORG_B)}
    headers = {**request.get("headers", {}), **dict.fromkeys(ORG_HEADERS, ORG_B)}
    steered = seen(api.request(**{**request, "params": params, "headers": headers}), volatile=case.volatile)
    assert steered == baseline, f"{route}: an org named in the query or headers changed the answer"
    if "json" in request:
        body = {**request["json"], **dict.fromkeys(ORG_PARAMS, ORG_B)}
        in_body = api.request(**{**request, "json": body})
        assert in_body.status_code == 422 or seen(in_body, volatile=case.volatile) == baseline, in_body.text
        assert_no_leak(in_body.text, f"{route} with org B named in the body")
