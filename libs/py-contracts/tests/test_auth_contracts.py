"""The login and `/me` contracts (PRD §3 roles, §7.8 Auth, §10 Auth)."""

import pytest
from onb_contracts import RequestModel, tenant_inputs_in_model
from onb_contracts.auth import BEARER, LoginRequest, Me, Role, Token
from pydantic import ValidationError


def test_the_roles_are_the_three_prd_roles() -> None:
    assert [role.value for role in Role] == ["HR_ADMIN", "HR", "MANAGER"]


def test_a_login_request_is_a_request_model_without_a_tenant_input() -> None:
    assert issubclass(LoginRequest, RequestModel)
    assert tenant_inputs_in_model(LoginRequest) == []


def test_a_login_request_refuses_an_org_id() -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"email": "a@example.com", "password": "x", "org_id": "org_globex"})


@pytest.mark.parametrize(
    "body",
    [
        {"email": "", "password": "x"},
        {"email": "a@example.com", "password": ""},
        {"email": "a" * 321, "password": "x"},
        {"email": "a@example.com", "password": "x" * 257},
        {"email": "a@example.com"},
    ],
)
def test_a_login_request_bounds_its_fields(body: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(body)


def test_a_token_is_a_bearer_token_with_an_expiry() -> None:
    token = Token.model_validate({"access_token": "abc", "expires_at": 1_900_000_000})
    assert token.token_type == BEARER
    assert BEARER.upper() == "BEARER"


def test_me_carries_the_role_and_org_from_the_token() -> None:
    me = Me(user_id="u1", org_id="org_acme", role=Role.MANAGER, name="Ravi", email="r@example.com")
    assert me.role is Role.MANAGER
    assert me.org_id == "org_acme"
