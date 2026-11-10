"""HS256 access tokens carrying `{sub, org_id, role, exp}` (PRD §7.8)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from onb_api.auth.settings import AuthSettings
from onb_api.auth.tokens import MAX_TOKEN_LENGTH, InvalidTokenError, Principal, issue_token, read_token
from onb_contracts.auth import Role

SETTINGS = AuthSettings(jwt_secret="k" * 64, bcrypt_rounds=4)
PRINCIPAL = Principal(user_id="u_acme_hr1", org_id="org_acme", role=Role.HR)


def _now() -> datetime:
    return datetime.now(UTC)


def _sign(claims: dict[str, Any], secret: str = SETTINGS.jwt_secret, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


def _valid_claims(**overrides: Any) -> dict[str, Any]:
    now = _now()
    claims: dict[str, Any] = {
        "sub": "u_acme_hr1",
        "org_id": "org_acme",
        "role": "HR",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_an_issued_token_reads_back_as_the_same_principal() -> None:
    token = issue_token(PRINCIPAL, SETTINGS, now=_now())
    assert read_token(token.access_token, SETTINGS) == PRINCIPAL


def test_an_issued_token_carries_exactly_sub_org_role_iat_exp_signed_hs256() -> None:
    now = _now()
    token = issue_token(PRINCIPAL, SETTINGS, now=now)
    header = jwt.get_unverified_header(token.access_token)
    claims = jwt.decode(token.access_token, SETTINGS.jwt_secret, algorithms=["HS256"])
    assert header["alg"] == "HS256"
    assert set(claims) == {"sub", "org_id", "role", "iat", "exp"}
    assert claims["exp"] == token.expires_at == int((now + SETTINGS.token_ttl).timestamp())


def test_an_expired_token_is_refused() -> None:
    token = issue_token(PRINCIPAL, SETTINGS, now=_now() - SETTINGS.token_ttl - timedelta(seconds=1))
    with pytest.raises(InvalidTokenError):
        read_token(token.access_token, SETTINGS)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_sign(_valid_claims(), secret="x" * 64), SETTINGS)


def test_a_token_with_a_changed_claim_is_refused() -> None:
    header, _, signature = issue_token(PRINCIPAL, SETTINGS, now=_now()).access_token.split(".")
    forged = base64.urlsafe_b64encode(json.dumps(_valid_claims(org_id="org_globex")).encode()).rstrip(b"=")
    with pytest.raises(InvalidTokenError):
        read_token(f"{header}.{forged.decode()}.{signature}", SETTINGS)


@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_a_token_with_another_algorithm_is_refused(algorithm: str) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_sign(_valid_claims(), algorithm=algorithm), SETTINGS)


def test_an_unsigned_token_is_refused() -> None:
    unsigned = jwt.encode(_valid_claims(), "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        read_token(unsigned, SETTINGS)


@pytest.mark.parametrize("missing", ["sub", "org_id", "role", "exp", "iat"])
def test_a_token_missing_a_claim_is_refused(missing: str) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_sign(_valid_claims(**{missing: None})), SETTINGS)


@pytest.mark.parametrize(
    "claim",
    [
        {"role": "SUPERUSER"},
        {"role": "hr"},
        {"org_id": "org$acme"},
        {"org_id": ""},
        {"org_id": 7},
        {"sub": ""},
        {"sub": 7},
    ],
)
def test_a_token_with_a_bad_claim_value_is_refused(claim: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_sign(_valid_claims(**claim)), SETTINGS)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * (MAX_TOKEN_LENGTH + 1)])
def test_garbage_is_refused(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(token, SETTINGS)


def test_the_error_names_no_claim_value() -> None:
    with pytest.raises(InvalidTokenError) as caught:
        read_token(_sign(_valid_claims(org_id="org$secret")), SETTINGS)
    assert "org$secret" not in str(caught.value)
