"""Orchestrator-signed grants (PRD §7.10 Signed grants): a JWS over
`{org_id, run_id, task_id, subject_id, fields, sink, exp ≤ 10 min, jti}`, Ed25519-signed with the
orchestrator's key. Sinks hold only the public key, so a sink (or the model) cannot mint or widen one.
"""

from __future__ import annotations

import json
import pickle
import secrets
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from onb_crypto import (
    GRANT_SIGNING_KEY_ENV,
    GRANT_VERIFY_KEYS_ENV,
    MAX_GRANT_TTL,
    GrantIssuer,
    GrantKeyError,
    GrantRejected,
    GrantSigningKey,
    GrantVerifier,
    GrantVerifyKey,
    RejectReason,
    SinkPolicyError,
    VaultField,
    load_sink_policy,
)
from onb_data import TenantViolationError, tenant_scope

from .fakes import ORG_A, ORG_B
from .grant_fakes import (
    EMAIL_SINK,
    HIRE_1,
    NOW,
    RUN,
    TASK,
    TELEGRAM,
    b64,
    claims,
    compact,
    header_for,
    hmac_forgery,
    sign,
    unb64,
    unsigned,
)
from .test_sink_policy import POLICY_FILE

POLICY = load_sink_policy(POLICY_FILE)


def make(key: GrantSigningKey | None = None) -> tuple[GrantSigningKey, GrantIssuer, GrantVerifier]:
    key = key or GrantSigningKey.generate()
    issuer = GrantIssuer(key, POLICY, clock=lambda: NOW)
    verifier = GrantVerifier([key.public_key], clock=lambda: NOW)
    return key, issuer, verifier


def issue(issuer: GrantIssuer, org: str = ORG_A, **kw: Any) -> str:
    args: dict[str, Any] = {
        "run_id": RUN,
        "task_id": TASK,
        "subject_id": HIRE_1,
        "fields": [VaultField.MOBILE],
        "sink": TELEGRAM,
        **kw,
    }
    with tenant_scope(org):
        return issuer.issue_grant(org, **args)


def rejected(verifier: GrantVerifier, grant: str) -> RejectReason:
    with pytest.raises(GrantRejected) as caught:
        verifier.verify(grant)
    return caught.value.reason


# --- issue and verify -------------------------------------------------------------------------------


def test_an_issued_grant_verifies_and_carries_exactly_the_prd_claims() -> None:
    _, issuer, verifier = make()
    grant = issue(issuer)
    payload = json.loads(unb64(grant.split(".")[1]))
    assert set(payload) == {"iss", "org_id", "run_id", "task_id", "subject_id", "fields", "sink", "iat", "exp", "jti"}
    verified = verifier.verify(grant)
    assert verified.org_id == ORG_A
    assert verified.run_id == RUN
    assert verified.task_id == TASK
    assert verified.subject_id == HIRE_1
    assert verified.fields == frozenset({VaultField.MOBILE})
    assert verified.sink == TELEGRAM
    assert verified.issued_at == NOW
    assert verified.expires_at == NOW + timedelta(minutes=5)


def test_the_header_names_eddsa_the_key_id_and_the_grant_type() -> None:
    key, issuer, _ = make()
    header = json.loads(unb64(issue(issuer).split(".")[0]))
    assert header == {"alg": "EdDSA", "kid": key.public_key.kid, "typ": "onb-pii-grant+jws"}


def test_the_default_lifetime_is_five_minutes_and_at_most_ten_is_allowed() -> None:
    _, issuer, verifier = make()
    assert verifier.verify(issue(issuer)).expires_at == NOW + timedelta(minutes=5)
    assert verifier.verify(issue(issuer, ttl=MAX_GRANT_TTL)).expires_at == NOW + timedelta(minutes=10)
    assert timedelta(minutes=10) == MAX_GRANT_TTL


@pytest.mark.parametrize("ttl", [timedelta(minutes=10, seconds=1), timedelta(hours=1), timedelta(0), timedelta(-1)])
def test_the_issuer_refuses_a_lifetime_outside_zero_to_ten_minutes(ttl: timedelta) -> None:
    _, issuer, _ = make()
    with pytest.raises(ValueError, match="10 minutes"):
        issue(issuer, ttl=ttl)


def test_every_grant_has_a_fresh_jti_from_secrets() -> None:
    _, issuer, verifier = make()
    with mock.patch("onb_crypto.grants.secrets.token_bytes", wraps=secrets.token_bytes) as spy:
        jtis = {verifier.verify(issue(issuer)).jti for _ in range(20)}
    assert len(jtis) == 20
    assert [c.args for c in spy.call_args_list] == [(16,)] * 20


def test_the_issuer_refuses_a_field_the_policy_does_not_allow_at_that_sink() -> None:
    _, issuer, _ = make()
    with pytest.raises(SinkPolicyError):
        issue(issuer, fields=[VaultField.AADHAAR])
    with pytest.raises(SinkPolicyError):
        issue(issuer, fields=[VaultField.EMAIL])  # email is not a telegram field
    with pytest.raises(SinkPolicyError):
        issue(issuer, sink="notify.sms")


def test_the_issuer_signs_only_for_the_tenant_context() -> None:
    _, issuer, _ = make()
    with tenant_scope(ORG_B), pytest.raises(TenantViolationError):
        issuer.issue_grant(
            ORG_A, run_id=RUN, task_id=TASK, subject_id=HIRE_1, fields=[VaultField.MOBILE], sink=TELEGRAM
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"run_id": ""},
        {"task_id": "has space"},
        {"subject_id": "x" * 129},
        {"fields": []},
        {"fields": ["mobile"]},
        {"fields": [VaultField.MOBILE, VaultField.MOBILE]},
        {"sink": "Notify"},
    ],
)
def test_the_issuer_refuses_malformed_arguments(bad: dict[str, Any]) -> None:
    _, issuer, _ = make()
    with pytest.raises((ValueError, SinkPolicyError)):
        issue(issuer, **bad)


def test_a_grant_for_two_fields_at_one_sink_is_possible_when_the_policy_allows(tmp_path: Any) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("version: 1\nsinks:\n  notify.both: [mobile, email]\n")
    key = GrantSigningKey.generate()
    issuer = GrantIssuer(key, load_sink_policy(path), clock=lambda: NOW)
    grant = issue(issuer, fields=[VaultField.MOBILE, VaultField.EMAIL], sink="notify.both")
    fields = GrantVerifier([key.public_key], clock=lambda: NOW).verify(grant).fields
    assert fields == frozenset({VaultField.MOBILE, VaultField.EMAIL})


# --- rejections at verification ---------------------------------------------------------------------


def test_bad_signature_another_key_is_rejected() -> None:
    _, _, verifier = make()
    _, other_issuer, _ = make()
    assert rejected(verifier, issue(other_issuer)) is RejectReason.BAD_SIGNATURE


def test_bad_signature_another_key_claiming_our_kid_is_rejected() -> None:
    key, _, verifier = make()
    other = GrantSigningKey.generate()
    forged = sign(other, claims(ORG_A), header=header_for(key))
    assert rejected(verifier, forged) is RejectReason.BAD_SIGNATURE


def test_bad_signature_a_flipped_signature_bit_is_rejected() -> None:
    _, issuer, verifier = make()
    head, body, sig = issue(issuer).split(".")
    raw = bytearray(unb64(sig))
    raw[0] ^= 1
    assert rejected(verifier, f"{head}.{body}.{b64(bytes(raw))}") is RejectReason.BAD_SIGNATURE


def test_bad_signature_a_widened_payload_under_the_old_signature_is_rejected() -> None:
    _, issuer, verifier = make()
    head, body, sig = issue(issuer).split(".")
    widened = {**json.loads(unb64(body)), "fields": ["mobile", "aadhaar"]}
    forged_body = b64(json.dumps(widened, separators=(",", ":")).encode())
    assert rejected(verifier, f"{head}.{forged_body}.{sig}") is RejectReason.BAD_SIGNATURE


def test_bad_signature_alg_none_is_rejected() -> None:
    key, _, verifier = make()
    assert rejected(verifier, unsigned(key, claims(ORG_A))) is RejectReason.BAD_SIGNATURE


def test_bad_signature_hs256_keyed_with_the_public_key_is_rejected() -> None:
    key, _, verifier = make()
    assert rejected(verifier, hmac_forgery(key, claims(ORG_A))) is RejectReason.BAD_SIGNATURE


@pytest.mark.parametrize(
    "header_change",
    [{"typ": "JWT"}, {"kid": "0" * 16}, {"crit": ["exp"]}, {"jku": "https://example.invalid/k"}],
)
def test_bad_signature_an_unexpected_header_is_rejected(header_change: dict[str, Any]) -> None:
    key, _, verifier = make()
    forged = sign(key, claims(ORG_A), header=header_for(key, **header_change))
    assert rejected(verifier, forged) is RejectReason.BAD_SIGNATURE


def test_expired_grant_is_rejected() -> None:
    key, issuer, _ = make()
    grant = issue(issuer)
    later = GrantVerifier([key.public_key], clock=lambda: NOW + timedelta(minutes=5))
    assert rejected(later, grant) is RejectReason.EXPIRED
    much_later = GrantVerifier([key.public_key], clock=lambda: NOW + timedelta(days=1))
    assert rejected(much_later, grant) is RejectReason.EXPIRED
    just_before = GrantVerifier([key.public_key], clock=lambda: NOW + timedelta(minutes=4, seconds=59))
    assert just_before.verify(grant).subject_id == HIRE_1


def test_expired_a_signed_grant_living_longer_than_ten_minutes_is_rejected() -> None:
    key, _, verifier = make()
    long_lived = claims(ORG_A, exp=int((NOW + timedelta(hours=1)).timestamp()))
    assert rejected(verifier, sign(key, long_lived)) is RejectReason.MALFORMED


def test_a_grant_issued_in_the_future_is_rejected() -> None:
    key, _, verifier = make()
    future = claims(
        ORG_A, iat=int((NOW + timedelta(minutes=2)).timestamp()), exp=int((NOW + timedelta(minutes=7)).timestamp())
    )
    assert rejected(verifier, sign(key, future)) is RejectReason.MALFORMED


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "onb.agent"},
        {"org_id": "bad org"},
        {"run_id": 7},
        {"subject_id": None},
        {"fields": []},
        {"fields": "mobile"},
        {"fields": ["mobile", "mobile"]},
        {"fields": ["phone"]},
        {"sink": "notify"},
        {"jti": "short"},
        {"iat": "now"},
        {"exp": True},
        {"exp": 1.5},
        {"extra": "claim"},
    ],
)
def test_a_signed_grant_with_malformed_claims_is_rejected(change: dict[str, Any]) -> None:
    key, _, verifier = make()
    assert rejected(verifier, sign(key, claims(ORG_A) | change)) is RejectReason.MALFORMED


@pytest.mark.parametrize("missing", ["org_id", "run_id", "task_id", "subject_id", "fields", "sink", "exp", "jti"])
def test_a_signed_grant_missing_a_claim_is_rejected(missing: str) -> None:
    key, _, verifier = make()
    payload = {k: v for k, v in claims(ORG_A).items() if k != missing}
    assert rejected(verifier, sign(key, payload)) is RejectReason.MALFORMED


@pytest.mark.parametrize(
    "grant",
    [
        "",
        "a.b",
        "a.b.c.d",
        "!!!.###.$$$",
        "x" * 5000,
        42,
        None,
        compact({"alg": "EdDSA"}, {}) + ".AA=",
        b64(b"not json") + "." + b64(b"{}") + "." + b64(b"x" * 64),
        b64(b"[]") + "." + b64(b"{}") + "." + b64(b"x" * 64),
    ],
)
def test_garbage_is_rejected_as_malformed_or_bad_signature(grant: Any) -> None:
    _, _, verifier = make()
    assert rejected(verifier, grant) in {RejectReason.MALFORMED, RejectReason.BAD_SIGNATURE}


def test_a_non_canonical_base64_signature_is_rejected() -> None:
    _, issuer, verifier = make()
    grant = issue(issuer)
    assert rejected(verifier, grant + "=") in {RejectReason.MALFORMED, RejectReason.BAD_SIGNATURE}


def test_a_rejection_message_never_echoes_the_grant() -> None:
    key, _, verifier = make()
    forged = sign(GrantSigningKey.generate(), claims(ORG_A), header=header_for(key))
    with pytest.raises(GrantRejected) as caught:
        verifier.verify(forged)
    assert forged.split(".")[1] not in str(caught.value)
    assert HIRE_1 not in str(caught.value)


def test_rotation_a_verifier_with_two_keys_accepts_both() -> None:
    old, new = GrantSigningKey.generate(), GrantSigningKey.generate()
    verifier = GrantVerifier([old.public_key, new.public_key], clock=lambda: NOW)
    for key in (old, new):
        issuer = GrantIssuer(key, POLICY, clock=lambda: NOW)
        assert verifier.verify(issue(issuer)).org_id == ORG_A


def test_a_verifier_needs_at_least_one_key_and_no_duplicates() -> None:
    key = GrantSigningKey.generate()
    with pytest.raises(GrantKeyError):
        GrantVerifier([])
    with pytest.raises(GrantKeyError):
        GrantVerifier([key.public_key, key.public_key])


# --- keys from the environment ----------------------------------------------------------------------


def test_keys_load_from_the_environment() -> None:
    key = GrantSigningKey.generate()
    env = {GRANT_SIGNING_KEY_ENV: key.encoded(), GRANT_VERIFY_KEYS_ENV: key.public_key.encoded()}
    assert GrantSigningKey.from_env(env).public_key == key.public_key
    issuer = GrantIssuer(GrantSigningKey.from_env(env), POLICY, clock=lambda: NOW)
    assert GrantVerifier.from_env(env, clock=lambda: NOW).verify(issue(issuer)).sink == TELEGRAM


def test_several_verify_keys_are_comma_separated() -> None:
    a, b = GrantSigningKey.generate(), GrantSigningKey.generate()
    env = {GRANT_VERIFY_KEYS_ENV: f"{a.public_key.encoded()}, {b.public_key.encoded()}"}
    verifier = GrantVerifier.from_env(env, clock=lambda: NOW)
    for key in (a, b):
        assert verifier.verify(issue(GrantIssuer(key, POLICY, clock=lambda: NOW))).org_id == ORG_A


@pytest.mark.parametrize("value", [None, "", "   ", "not-base64!", b64(b"short"), b64(b"x" * 33)])
def test_a_missing_or_malformed_signing_key_fails_at_startup(value: str | None) -> None:
    env = {} if value is None else {GRANT_SIGNING_KEY_ENV: value}
    with pytest.raises(GrantKeyError) as caught:
        GrantSigningKey.from_env(env)
    if value and value.strip():
        assert value.strip() not in str(caught.value)


@pytest.mark.parametrize("value", [None, "", ",", "not-base64!", b64(b"x" * 31)])
def test_a_missing_or_malformed_verify_key_fails_at_startup(value: str | None) -> None:
    env = {} if value is None else {GRANT_VERIFY_KEYS_ENV: value}
    with pytest.raises(GrantKeyError):
        GrantVerifier.from_env(env)


def test_the_signing_key_never_shows_its_material() -> None:
    key = GrantSigningKey.generate()
    assert key.encoded() not in repr(key)
    assert key.encoded() not in str(key)
    assert "redacted" in repr(key)
    with pytest.raises(TypeError):
        pickle.dumps(key)


def test_verify_keys_round_trip_and_have_a_stable_kid() -> None:
    key = GrantSigningKey.generate()
    again = GrantVerifyKey.from_encoded(key.public_key.encoded())
    assert again == key.public_key
    assert again.kid == key.public_key.kid
    assert len(again.kid) == 16
    assert GrantSigningKey.generate().public_key.kid != key.public_key.kid


def test_a_grant_never_holds_a_value_only_ids_and_field_names() -> None:
    _, issuer, _ = make()
    grant = issue(issuer, fields=[VaultField.EMAIL], sink=EMAIL_SINK)
    payload = json.loads(unb64(grant.split(".")[1]))
    assert payload["fields"] == ["email"]
    assert all(isinstance(v, str | int | list) for v in payload.values())
