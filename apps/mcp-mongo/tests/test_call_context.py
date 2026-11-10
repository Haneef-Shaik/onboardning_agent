"""The hub-signed call context: the only source of tenant scope for mcp-mongo (PRD §7.4, §7.5)."""

from __future__ import annotations

import time

import jwt
import pytest
from onb_mcp_mongo.call_context import (
    AUDIENCE,
    ISSUER,
    MAX_LIFETIME_S,
    CallContext,
    CallContextError,
    sign_call_context,
    verify_call_context,
)

KEY = "k" * 48
OTHER_KEY = "x" * 48


def _context(**overrides: object) -> CallContext:
    fields: dict[str, object] = {
        "org_id": "org_a",
        "agent": "db_agent",
        "run_id": "run_1",
        "tool": "find",
        "read": frozenset({"new_hires", "run_tasks"}),
        "write": frozenset({"run_tasks"}),
    }
    fields.update(overrides)
    return CallContext(**fields)  # type: ignore[arg-type]


def _raw_token(claims: dict[str, object], key: str = KEY) -> str:
    return jwt.encode(claims, key, algorithm="HS256")


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 60,
        "org_id": "org_a",
        "agent": "db_agent",
        "run_id": "run_1",
        "tool": "find",
        "collections": {"read": ["new_hires"], "write": []},
    }
    claims.update(overrides)
    return claims


def test_a_signed_context_round_trips() -> None:
    ctx = _context()
    assert verify_call_context(sign_call_context(ctx, KEY), KEY) == ctx


def test_a_context_signed_with_another_key_is_refused() -> None:
    with pytest.raises(CallContextError):
        verify_call_context(sign_call_context(_context(), OTHER_KEY), KEY)


def test_a_missing_context_is_refused() -> None:
    with pytest.raises(CallContextError):
        verify_call_context(None, KEY)
    with pytest.raises(CallContextError):
        verify_call_context("", KEY)


def test_an_expired_context_is_refused() -> None:
    past = int(time.time()) - 120
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(_claims(iat=past - 60, exp=past)), KEY)


def test_a_few_seconds_of_clock_skew_are_tolerated() -> None:
    now = int(time.time())
    assert verify_call_context(_raw_token(_claims(iat=now + 3, exp=now + 60)), KEY).org_id == "org_a"


def test_a_context_living_longer_than_the_maximum_is_refused() -> None:
    now = int(time.time())
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(_claims(iat=now, exp=now + MAX_LIFETIME_S + 1)), KEY)


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "org_id", "agent", "run_id", "tool", "collections"])
def test_a_context_missing_a_claim_is_refused(missing: str) -> None:
    claims = _claims()
    del claims[missing]
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(claims), KEY)


def test_a_context_for_another_audience_or_issuer_is_refused() -> None:
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(_claims(aud="mcp-mocks")), KEY)
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(_claims(iss="orchestrator")), KEY)


def test_an_unsigned_context_is_refused() -> None:
    token = jwt.encode(_claims(), "", algorithm="none")
    with pytest.raises(CallContextError):
        verify_call_context(token, KEY)


@pytest.mark.parametrize(
    "overrides",
    [
        {"org_id": "org.$a"},
        {"org_id": ""},
        {"agent": ""},
        {"agent": 7},
        {"run_id": ""},
        {"tool": ""},
        {"tool": "drop$"},
        {"collections": {"read": "new_hires", "write": []}},
        {"collections": {"read": ["$cmd"], "write": []}},
        {"collections": {"read": [], "write": ["system.users"]}},
        {"collections": ["new_hires"]},
    ],
)
def test_a_malformed_context_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(CallContextError):
        verify_call_context(_raw_token(_claims(**overrides)), KEY)


def test_a_signing_key_must_be_long_enough() -> None:
    with pytest.raises(ValueError, match="32"):
        sign_call_context(_context(), "short")
    with pytest.raises(ValueError, match="32"):
        verify_call_context("token", "short")


def test_the_refusal_never_echoes_the_token() -> None:
    token = sign_call_context(_context(), OTHER_KEY)
    with pytest.raises(CallContextError) as caught:
        verify_call_context(token, KEY)
    assert token not in str(caught.value)


def test_readable_collections_include_the_writable_ones() -> None:
    ctx = _context(read=frozenset({"new_hires"}), write=frozenset({"run_tasks"}))
    assert ctx.readable == frozenset({"new_hires", "run_tasks"})
