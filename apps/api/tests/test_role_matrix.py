"""Each role x each guarded route gives the expected 200/403 (AUTH-1 box 3, PRD §3 roles).

`ROLE_MATRIX` is the written-down expectation, independent of the code. The completeness tests fail
when a route is added without a row here, or without the token dependency, so every later route
joins the matrix. `require_role` itself is exercised on probe routes with every role combination.
"""

import asyncio
import itertools
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import pytest
import yaml
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry
from onb_api import create_app
from onb_api.auth.dependencies import authenticated, require_role
from onb_api.auth.tokens import Principal
from onb_api.responses import respond
from onb_api.templates import RegistryValidatorSource, TemplateLifecycle
from onb_api.testing import InMemoryAuditLog, InMemoryTemplateStore, bearer, token_for
from onb_api.testing_evals import eval_report, store_eval_run
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts import Envelope
from onb_contracts.auth import Role
from onb_contracts.templates import Template
from onb_data import tenant_scope

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
    # HIRE-1 (PRD §3, §7.10, §10 Hires). Routes with a body get none here: a permitted role reaches
    # validation (422), any other role is refused before it (403).
    ("POST", "/hires"): {Role.HR_ADMIN: 422, Role.HR: 422, Role.MANAGER: 403},
    ("GET", "/hires"): ALL_200,
    ("GET", "/hires/{hire_id}"): ALL_200,  # h_acme_001 is Ravi's report
    ("PUT", "/hires/{hire_id}/pii/{field}"): {Role.HR_ADMIN: 422, Role.HR: 422, Role.MANAGER: 403},
    ("POST", "/hires/{hire_id}/pii/{field}/reveal"): {Role.HR_ADMIN: 422, Role.HR: 422, Role.MANAGER: 403},
    ("GET", "/pii-access-log"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    # TPL-2: HR and HR_ADMIN author drafts; only HR_ADMIN publishes and archives; everyone reads the
    # published templates (managers open them in suggest mode, FR-T6) (PRD §3, FR-T4, §10 Templates).
    ("POST", "/templates/drafts"): {Role.HR_ADMIN: 201, Role.HR: 201, Role.MANAGER: 403},
    ("GET", "/templates/drafts"): {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403},
    ("GET", "/templates/drafts/{draft_id}"): {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403},
    ("PUT", "/templates/drafts/{draft_id}"): {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403},
    ("POST", "/templates/drafts/{draft_id}/validate"): {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403},
    ("POST", "/templates/drafts/{draft_id}/publish"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    ("GET", "/templates"): ALL_200,
    ("GET", "/templates/{key}/versions/{version}"): ALL_200,
    ("POST", "/templates/{key}/versions/{version}/archive"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    # HIRE-2 (PRD FR-P3, §10 Hires): sent without `type` or a body, a permitted role reaches validation.
    ("POST", "/hires/{hire_id}/documents"): {Role.HR_ADMIN: 422, Role.HR: 422, Role.MANAGER: 403},
    # UI-8: eval runs are HR_ADMIN only, read-only (PRD §10 Evals).
    ("GET", "/evals/runs"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
    ("GET", "/evals/runs/{run_id}"): {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403},
}

CONFIG = Path(__file__).resolve().parents[3] / "config"
SEED_TEMPLATE: dict[str, Any] = yaml.safe_load((CONFIG.parent / "templates" / "engineer-india.yaml").read_text())
SAMPLE_DRAFT = "tdr_" + "a" * 32  # seeded unpublished
PUBLISHED_DRAFT = "tdr_" + "b" * 32  # seeded and published as engineer-india v1
SAMPLE_EVAL_RUN = "evr_20261102T090000Z_0000aaa1"

# A concrete path for each route with path parameters: an existing record, so HR_ADMIN gets 200.
SAMPLE_PATHS = {
    "/agents/{name}": "/agents/db_agent",
    "/hires/{hire_id}": "/hires/h_acme_001",
    "/hires/{hire_id}/pii/{field}": "/hires/h_acme_001/pii/mobile",
    "/hires/{hire_id}/pii/{field}/reveal": "/hires/h_acme_001/pii/mobile/reveal",
    "/templates/drafts/{draft_id}": f"/templates/drafts/{SAMPLE_DRAFT}",
    "/templates/drafts/{draft_id}/validate": f"/templates/drafts/{SAMPLE_DRAFT}/validate",
    "/templates/drafts/{draft_id}/publish": f"/templates/drafts/{SAMPLE_DRAFT}/publish",
    "/templates/{key}/versions/{version}": "/templates/engineer-india/versions/1",
    "/templates/{key}/versions/{version}/archive": "/templates/engineer-india/versions/1/archive",
    "/hires/{hire_id}/documents": "/hires/h_acme_001/documents",
    "/evals/runs/{run_id}": f"/evals/runs/{SAMPLE_EVAL_RUN}",
}

# A valid body for the routes whose permitted roles should succeed (the hire routes are sent none).
SAMPLE_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/templates/drafts"): {"template": SEED_TEMPLATE},
    ("PUT", "/templates/drafts/{draft_id}"): {"template": SEED_TEMPLATE},
}


@pytest.fixture
def templates(
    template_store: InMemoryTemplateStore, registry: AgentRegistry, audit_log: InMemoryAuditLog
) -> TemplateLifecycle:
    """The lifecycle with one draft and one published version in org_acme, at the sample paths."""
    ids = itertools.chain([SAMPLE_DRAFT, PUBLISHED_DRAFT], (f"tdr_{uuid.uuid4().hex}" for _ in itertools.count()))
    validators = RegistryValidatorSource.from_config(registry, CONFIG)
    lifecycle = TemplateLifecycle(template_store, validators, audit_log, new_draft_id=lambda: next(ids))

    async def seed() -> None:
        with tenant_scope("org_acme"):
            template = Template.model_validate(SEED_TEMPLATE)
            await lifecycle.create_draft(template, actor_id="u_acme_hr1")
            await lifecycle.create_draft(template, actor_id="u_acme_hr1")
            await lifecycle.publish(PUBLISHED_DRAFT, actor_id="u_acme_hradmin")

    asyncio.run(seed())
    return lifecycle


@pytest.fixture(autouse=True)
def _sample_eval_run(memory_db: MemoryDatabase) -> None:
    """One acme eval run, as the harness stores it, so the eval run sample path exists."""
    started = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    report = eval_report(SAMPLE_EVAL_RUN, started, started)
    asyncio.run(store_eval_run(memory_store(memory_db), "org_acme", report))


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
    response = client.request(
        method,
        SAMPLE_PATHS.get(path, path),
        headers=bearer(token_for(user_id, org_id, role)),
        json=SAMPLE_BODIES.get(route),
    )
    assert response.status_code == ROLE_MATRIX[route][role], response.text


@pytest.mark.parametrize("route", sorted(ROLE_MATRIX), ids=lambda k: f"{k[0]} {k[1]}")
def test_every_guarded_route_refuses_a_missing_token(client: TestClient, route: tuple[str, str]) -> None:
    method, path = route
    assert client.request(method, SAMPLE_PATHS.get(path, path), json=SAMPLE_BODIES.get(route)).status_code == 401


def test_every_path_parameter_route_has_a_sample_path() -> None:
    assert {path for _, path in ROLE_MATRIX if "{" in path} == set(SAMPLE_PATHS)


def test_every_route_with_a_body_has_a_sample_body_or_expects_422_without_one() -> None:
    """A body route either gets a valid sample body (its permitted roles get 2xx), or is sent none and
    its matrix row says the permitted roles reach validation (422), as HIRE-1's rows do."""
    with_body = {
        key
        for route in _routes(create_app())
        for key in _keys(route)
        if key not in PUBLIC_ROUTES and route.body_field is not None
    }
    assert set(SAMPLE_BODIES) <= with_body
    for key in with_body - set(SAMPLE_BODIES):
        assert 422 in ROLE_MATRIX[key].values(), f"{key}: give it a SAMPLE_BODIES entry or expect 422"


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
