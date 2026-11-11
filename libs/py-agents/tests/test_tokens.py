"""The short-lived agent token (PRD §7.4): issued by the orchestrator per task, verified by the MCP hub."""

from __future__ import annotations

import time

import jwt
import pytest
from onb_agents.tokens import (
    AUDIENCE,
    ISSUER,
    MAX_LIFETIME_S,
    AgentClaims,
    AgentTokenError,
    hub_client_config,
    issue_agent_token,
    verify_agent_token,
)

KEY = "unit-test-agent-token-key-0123456789abcdef"
OTHER_KEY = "another-agent-token-key-0123456789abcdefgh"
CLAIMS = AgentClaims(agent="db_agent", run_id="run_1", org_id="org_acme", scopes=("mongo__find", "mongo__count"))


def test_an_issued_token_verifies_to_the_same_claims() -> None:
    token = issue_agent_token(CLAIMS, KEY, lifetime_s=600)
    verified = verify_agent_token(token, KEY)
    assert verified.claims == CLAIMS
    assert verified.expires_at - verified.issued_at == 600
    assert len(verified.jti) == 32


def test_the_lifetime_is_at_most_fifteen_minutes() -> None:
    assert MAX_LIFETIME_S == 900
    with pytest.raises(ValueError, match="lifetime"):
        issue_agent_token(CLAIMS, KEY, lifetime_s=901)
    with pytest.raises(ValueError, match="lifetime"):
        issue_agent_token(CLAIMS, KEY, lifetime_s=0)


def test_an_expired_token_is_refused_but_its_genuine_claims_are_kept_for_the_audit() -> None:
    now = int(time.time())
    token = issue_agent_token(CLAIMS, KEY, lifetime_s=60, now=now - 600)
    with pytest.raises(AgentTokenError) as caught:
        verify_agent_token(token, KEY)
    assert caught.value.reason == "token_expired"
    assert caught.value.claims == CLAIMS  # signature checked: the org is genuine, so the denial can be audited


def test_a_token_signed_with_another_key_is_refused_without_claims() -> None:
    token = issue_agent_token(CLAIMS, OTHER_KEY)
    with pytest.raises(AgentTokenError) as caught:
        verify_agent_token(token, KEY)
    assert caught.value.reason == "token_invalid"
    assert caught.value.claims is None
    assert token not in str(caught.value)


def _forge(claims: dict[str, object], key: str = KEY, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


def _base(**over: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 60,
        "jti": "a" * 32,
        "agent": "db_agent",
        "run_id": "run_1",
        "org_id": "org_acme",
        "scopes": ["mongo__find"],
    }
    claims.update(over)
    return claims


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        (_base(exp=int(time.time()) + 3600), "token_invalid"),  # lives longer than 15 minutes
        (_base(aud="mcp-mongo"), "token_invalid"),
        (_base(iss="someone"), "token_invalid"),
        (_base(org_id="org acme"), "token_invalid"),
        (_base(org_id=["org_acme"]), "token_invalid"),
        (_base(scopes="mongo__find"), "token_invalid"),
        (_base(scopes=["bad scope"]), "token_invalid"),
        (_base(agent=""), "token_invalid"),
        ({k: v for k, v in _base().items() if k != "run_id"}, "token_invalid"),
        ({k: v for k, v in _base().items() if k != "exp"}, "token_invalid"),
    ],
    ids=["too-long", "audience", "issuer", "org-space", "org-list", "scopes-str", "scope-bad", "agent", "run", "exp"],
)
def test_malformed_tokens_are_refused(claims: dict[str, object], reason: str) -> None:
    with pytest.raises(AgentTokenError) as caught:
        verify_agent_token(_forge(claims), KEY)
    assert caught.value.reason == reason
    assert caught.value.claims is None


def test_the_none_algorithm_is_refused() -> None:
    token = jwt.encode(_base(), key=None, algorithm="none")
    with pytest.raises(AgentTokenError):
        verify_agent_token(token, KEY)


def test_a_missing_or_empty_token_is_refused() -> None:
    for token in (None, ""):
        with pytest.raises(AgentTokenError) as caught:
            verify_agent_token(token, KEY)
        assert caught.value.reason == "no_token"


def test_a_short_key_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        issue_agent_token(CLAIMS, "short")
    with pytest.raises(ValueError, match="32"):
        verify_agent_token("x", "short")


def test_claims_are_validated_at_issue() -> None:
    with pytest.raises(ValueError, match="malformed"):
        AgentClaims(agent="db agent", run_id="run_1", org_id="org_acme", scopes=())
    with pytest.raises(ValueError, match="malformed"):
        AgentClaims(agent="db_agent", run_id="run_1", org_id="org/acme", scopes=())
    with pytest.raises(ValueError, match="malformed"):
        AgentClaims(agent="db_agent", run_id="run_1", org_id="org_acme", scopes=("x y",))


def test_the_agent_side_config_holds_only_the_hub_url_and_the_agent_token() -> None:
    token = issue_agent_token(CLAIMS, KEY)
    config = hub_client_config("http://mcp-hub:8100/mcp", token)
    assert config == {"url": "http://mcp-hub:8100/mcp", "headers": {"Authorization": f"Bearer {token}"}}
    assert KEY not in repr(config)
