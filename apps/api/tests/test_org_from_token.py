"""The org comes only from the signed token; an `org_id` sent anywhere else is rejected or ignored
(AUTH-1 box 2, PRD §7.9 Identity)."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from onb_api.auth.dependencies import authenticated
from onb_api.auth.tokens import Principal
from onb_api.responses import respond
from onb_contracts import Envelope
from onb_contracts.auth import Role
from onb_data import current_tenant

from .auth_fakes import PASSWORD, bearer, email_of, token_for

ACME_HR = token_for("u_acme_hr1", "org_acme", Role.HR)


def test_a_login_body_with_org_id_is_rejected(client: TestClient) -> None:
    body = {"email": email_of("u_acme_hr1"), "password": PASSWORD, "org_id": "org_globex"}
    response = client.post("/auth/login", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "body.<extra>", "issue": "Unknown field"}]


@pytest.mark.parametrize("key", ["org_id", "orgId", "tenant", "org"])
def test_a_login_body_with_any_tenant_key_is_rejected(client: TestClient, key: str) -> None:
    body = {"email": email_of("u_acme_hr1"), "password": PASSWORD, key: "org_globex"}
    assert client.post("/auth/login", json=body).status_code == 422


@pytest.mark.parametrize("query", ["org_id=org_globex", "orgId=org_globex", "tenant=org_globex"])
def test_an_org_id_in_the_query_is_ignored(client: TestClient, query: str) -> None:
    response = client.get(f"/me?{query}", headers=bearer(ACME_HR))
    assert response.status_code == 200
    assert response.json()["data"]["org_id"] == "org_acme"


@pytest.mark.parametrize("header", ["X-Org-Id", "X-Tenant", "Org-Id"])
def test_an_org_id_in_a_header_is_ignored(client: TestClient, header: str) -> None:
    response = client.get("/me", headers={**bearer(ACME_HR), header: "org_globex"})
    assert response.status_code == 200
    assert response.json()["data"]["org_id"] == "org_acme"


def test_an_org_id_in_the_path_finds_no_route(client: TestClient) -> None:
    assert client.get("/orgs/org_globex/me", headers=bearer(ACME_HR)).status_code == 404
    assert client.get("/me/org_globex", headers=bearer(ACME_HR)).status_code == 404


def test_a_user_of_another_org_cannot_be_reached_by_naming_it(client: TestClient) -> None:
    """A token for org_acme naming a globex user id as `sub` finds no user in org_acme: 401."""
    forged_sub = token_for("u_globex_hr1", "org_acme", Role.HR)
    assert client.get("/me", headers=bearer(forged_sub)).status_code == 401


def test_the_route_runs_inside_the_tokens_tenant_scope(app: FastAPI, client: TestClient) -> None:
    async def which_org(
        principal: Annotated[Principal, Depends(authenticated)], org_id: str = "ignored"
    ) -> Envelope[dict[str, str]]:
        return respond({"scope": current_tenant().org_id, "token": principal.org_id, "query": org_id})

    app.add_api_route("/test/which-org", which_org, include_in_schema=False)
    token = token_for("u_globex_hr1", "org_globex", Role.HR)
    data = client.get("/test/which-org?org_id=org_acme", headers=bearer(token)).json()["data"]
    assert data == {"scope": "org_globex", "token": "org_globex", "query": "org_acme"}


def test_the_tenant_scope_ends_with_the_request(app: FastAPI, client: TestClient) -> None:
    from onb_data import NoTenantContextError

    seen: list[str] = []

    async def unscoped() -> Envelope[None]:
        try:
            seen.append(current_tenant().org_id)
        except NoTenantContextError:
            seen.append("none")
        return respond(None)

    app.add_api_route("/test/unscoped", unscoped, include_in_schema=False)
    client.get("/me", headers=bearer(ACME_HR))
    client.get("/test/unscoped")
    assert seen == ["none"]
