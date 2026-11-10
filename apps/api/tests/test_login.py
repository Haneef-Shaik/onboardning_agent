"""`POST /auth/login` and `GET /me`; every authentication failure is 401 (AUTH-1 box 1, PRD §7.8)."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

import jwt
import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.auth.tokens import LEEWAY_SECONDS, read_token
from onb_api.testing import PASSWORD, TEST_SETTINGS, InMemoryUserStore, bearer, email_of, token_for
from onb_contracts.auth import BEARER, Role

ACME_HR = ("u_acme_hr1", "org_acme", Role.HR)


class _Response(Protocol):
    """What `_assert_401` reads from a TestClient response.

    Starlette's TestClient returns `httpx2.Response` when httpx2 is installed and `httpx.Response`
    otherwise; both match this shape, so the helper does not depend on which one is present.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def json(self) -> object: ...


def _assert_401(response: _Response) -> None:
    assert response.status_code == 401
    body = response.json()
    assert isinstance(body, dict)
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


# --- login ------------------------------------------------------------------------------------


def test_login_with_the_right_password_returns_a_token_for_the_users_org_and_role(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": PASSWORD})
    assert response.status_code == 200
    token = response.json()["data"]
    assert token["token_type"] == BEARER
    principal = read_token(token["access_token"], TEST_SETTINGS)
    assert (principal.user_id, principal.org_id, principal.role) == ACME_HR


def test_login_matches_the_email_ignoring_case_and_spaces(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": "  U_ACME_HR1@Example.COM ", "password": PASSWORD})
    assert response.status_code == 200


def test_login_with_a_wrong_password_returns_401(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": PASSWORD + "x"})
    _assert_401(response)
    assert response.json()["error"]["message"] == "Authentication required"


def test_login_with_an_unknown_email_answers_exactly_like_a_wrong_password(client: TestClient) -> None:
    wrong_password = client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": "nope"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "nope"})
    _assert_401(unknown)
    strip = {"meta"}
    assert {k: v for k, v in unknown.json().items() if k not in strip} == {
        k: v for k, v in wrong_password.json().items() if k not in strip
    }


def test_login_with_a_password_longer_than_bcrypt_reads_returns_401(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": PASSWORD + "x" * 80})
    _assert_401(response)


def test_login_for_a_user_whose_record_is_gone_returns_401(client: TestClient, users: InMemoryUserStore) -> None:
    users.remove_user_record("org_acme", "u_acme_hr1")
    _assert_401(client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": PASSWORD}))


def test_login_without_a_user_store_is_service_unavailable() -> None:
    with TestClient(create_app(auth=TEST_SETTINGS)) as client:
        response = client.post("/auth/login", json={"email": email_of("u_acme_hr1"), "password": PASSWORD})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# --- /me and the token check ---------------------------------------------------------------------


def test_me_with_a_valid_token_returns_the_user(client: TestClient) -> None:
    response = client.get("/me", headers=bearer(token_for(*ACME_HR)))
    assert response.status_code == 200
    assert response.json()["data"] == {
        "user_id": "u_acme_hr1",
        "org_id": "org_acme",
        "role": "HR",
        "name": "Sameer Khan",
        "email": email_of("u_acme_hr1"),
    }


def test_me_reports_the_stored_role_not_the_tokens(client: TestClient, users: InMemoryUserStore) -> None:
    """No revocation in the MVP: the token still works until it expires, but /me shows the current role."""
    token = token_for(*ACME_HR)
    users.set_role("org_acme", "u_acme_hr1", Role.MANAGER)
    response = client.get("/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "MANAGER"


def test_startup_makes_the_user_store_indexes(users: InMemoryUserStore) -> None:
    before = users.indexes_ensured
    with TestClient(create_app(auth=TEST_SETTINGS, users=users)):
        pass
    assert users.indexes_ensured == before + 1


def test_a_token_from_login_opens_me(client: TestClient) -> None:
    login = client.post("/auth/login", json={"email": email_of("u_globex_mgr1"), "password": PASSWORD})
    token = login.json()["data"]["access_token"]
    me = client.get("/me", headers=bearer(token)).json()["data"]
    assert (me["user_id"], me["org_id"], me["role"]) == ("u_globex_mgr1", "org_globex", "MANAGER")


def test_missing_token_returns_401(client: TestClient) -> None:
    _assert_401(client.get("/me"))


def test_expired_token_returns_401(client: TestClient) -> None:
    issued = datetime.now(UTC) - TEST_SETTINGS.token_ttl - timedelta(seconds=LEEWAY_SECONDS + 1)
    _assert_401(client.get("/me", headers=bearer(token_for(*ACME_HR, now=issued))))


def test_tampered_token_returns_401(client: TestClient) -> None:
    header, payload, signature = token_for(*ACME_HR).split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    _assert_401(client.get("/me", headers=bearer(f"{header}.{payload}.{flipped}")))


def test_token_signed_with_another_secret_returns_401(client: TestClient) -> None:
    now = int(datetime.now(UTC).timestamp())
    claims = {"sub": "u_acme_hradmin", "org_id": "org_acme", "role": "HR_ADMIN", "iat": now, "exp": now + 60}
    claims |= {"iss": "onb-api", "aud": "onb-api"}
    forged = jwt.encode(claims, "an-attacker-chosen-secret-that-is-long-enough!!", algorithm="HS256")
    _assert_401(client.get("/me", headers=bearer(forged)))


@pytest.mark.parametrize(
    "authorization",
    ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "Token abc", "bearer-ish abc", "Bearer not.a.jwt"],
)
def test_a_malformed_authorization_header_returns_401(client: TestClient, authorization: str) -> None:
    _assert_401(client.get("/me", headers={"Authorization": authorization}))


def test_a_valid_token_for_a_user_whose_record_is_gone_returns_401(
    client: TestClient, users: InMemoryUserStore
) -> None:
    users.remove_user_record("org_acme", "u_acme_hr1")
    _assert_401(client.get("/me", headers=bearer(token_for(*ACME_HR))))


def test_the_401_answer_carries_no_token_detail(client: TestClient) -> None:
    issued = datetime.now(UTC) - TEST_SETTINGS.token_ttl - timedelta(seconds=LEEWAY_SECONDS + 1)
    body = client.get("/me", headers=bearer(token_for(*ACME_HR, now=issued))).json()
    assert body["error"]["message"] == "Authentication required"
    assert "details" not in body["error"] or not body["error"]["details"]
