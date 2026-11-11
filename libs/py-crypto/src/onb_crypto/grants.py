"""Orchestrator-signed grants (PRD §7.10 Signed grants, FR-P5, §8.1 Tool).

When a task that declares `pii_access` starts, the orchestrator (never the LLM) issues a grant: a
compact JWS, Ed25519-signed with the orchestrator's key, over

    {iss, org_id, run_id, task_id, subject_id, fields, sink, iat, exp, jti}      exp - iat ≤ 10 min

It travels in the tool-call context, out of band. A sink checks it with `GrantVerifier`, which accepts
exactly one header shape (`alg: EdDSA`, a known `kid`, our `typ`) and exactly these claims. Anything
else raises `GrantRejected` with a `RejectReason`; messages never echo the grant or its claims.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from onb_data import TenantContext, TenantViolationError, current_tenant

from onb_crypto.errors import CryptoError
from onb_crypto.grant_keys import GrantKeyError, GrantSigningKey, GrantVerifyKey, verify_keys_from_env
from onb_crypto.sink_policy import SinkPolicy, SinkPolicyError, check_sink_id
from onb_crypto.vault_record import VaultField

__all__ = [
    "DEFAULT_GRANT_TTL",
    "MAX_GRANT_TTL",
    "Grant",
    "GrantIssuer",
    "GrantRejected",
    "GrantVerifier",
    "RejectReason",
]

MAX_GRANT_TTL: Final = timedelta(minutes=10)
DEFAULT_GRANT_TTL: Final = timedelta(minutes=5)
ISSUER: Final = "onb.orchestrator"
_ALG: Final = "EdDSA"
_TYP: Final = "onb-pii-grant+jws"
_CLOCK_SKEW: Final = timedelta(seconds=30)
_MIN_GRANT_TTL: Final = timedelta(seconds=1)
_MAX_NUMERIC_DATE: Final = 2**33  # year 2242: bounds fromtimestamp
_MAX_GRANT_CHARS: Final = 4096
_JTI_BYTES: Final = 16
_JTI_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{22}")
# run, task and subject ids: no spaces or separators a log line or query could misread.
_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_CLAIMS: Final = frozenset({"iss", "org_id", "run_id", "task_id", "subject_id", "fields", "sink", "iat", "exp", "jti"})
_FIELDS_BY_NAME: Final = {field.value: field for field in VaultField}


class RejectReason(StrEnum):
    MALFORMED = "malformed"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    WRONG_SINK = "wrong_sink"
    WRONG_ORG = "wrong_org"
    FIELD_NOT_GRANTED = "field_not_granted"
    FIELD_FORBIDDEN_BY_POLICY = "field_forbidden_by_policy"
    REPLAYED = "replayed"
    NOT_FOUND = "token_not_found"
    NOT_BOUND = "token_not_bound"
    SUPERSEDED = "token_superseded"
    PURGED = "token_purged"
    RECORD_INTEGRITY = "record_integrity"
    VAULT_ERROR = "vault_error"
    # NOTIF-1: the grant is for another run than the hub-signed call context names (checked by the sink).
    WRONG_RUN = "wrong_run"


class GrantRejected(CryptoError):  # noqa: N818 - the name the PII-4 card and later goals use
    """A grant, or a token presented with it, is refused. Nothing is released."""

    def __init__(self, reason: RejectReason, detail: str) -> None:
        super().__init__(f"grant rejected ({reason.value}): {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Grant:
    """A verified grant's claims."""

    org_id: str
    run_id: str
    task_id: str
    subject_id: str
    fields: frozenset[VaultField]
    sink: str
    issued_at: datetime
    expires_at: datetime
    jti: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    """Strict base64url without padding: anything that does not re-encode identically is refused."""
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise GrantRejected(RejectReason.MALFORMED, "not base64url") from None
    if _b64(raw) != text:
        raise GrantRejected(RejectReason.MALFORMED, "not canonical base64url")
    return raw


def _json_part(text: str) -> dict[str, Any]:
    try:
        value = json.loads(_unb64(text))
    except (ValueError, UnicodeDecodeError):
        raise GrantRejected(RejectReason.MALFORMED, "a part is not JSON") from None
    if not isinstance(value, dict):
        raise GrantRejected(RejectReason.MALFORMED, "a part is not a JSON object")
    return value


def _encode_part(value: Mapping[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _check_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{what} must be 1-128 letters, digits or _ . : -")
    return value


def _check_org(org_id: object) -> str:
    try:
        valid = TenantContext(org_id).org_id  # type: ignore[arg-type]  # validated right here
    except ValueError:
        raise ValueError("invalid org id: 1-128 letters, digits, '_' or '-'") from None
    if current_tenant().org_id != valid:
        raise TenantViolationError("grant issued for an org other than the tenant context")
    return valid


def _check_fields(fields: Iterable[object]) -> list[VaultField]:
    listed = list(fields)
    if not listed or not all(isinstance(f, VaultField) for f in listed):
        raise ValueError("a grant needs at least one VaultField")
    if len(set(listed)) != len(listed):
        raise ValueError("a grant lists a field twice")
    return sorted(listed, key=lambda f: f.value)  # type: ignore[arg-type]  # all VaultField, checked above


class GrantIssuer:
    """The orchestrator's side: signs grants. Only the orchestrator process holds a `GrantSigningKey`."""

    def __init__(self, key: GrantSigningKey, policy: SinkPolicy, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._key = key
        self._policy = policy
        self._clock = clock

    def issue_grant(
        self,
        org_id: str,
        *,
        run_id: str,
        task_id: str,
        subject_id: str,
        fields: Sequence[VaultField],
        sink: str,
        ttl: timedelta = DEFAULT_GRANT_TTL,
    ) -> str:
        """A grant letting `sink` resolve `fields` of `subject_id` for this run's task, for `ttl`."""
        org_id = _check_org(org_id)
        for value, name in ((run_id, "run_id"), (task_id, "task_id"), (subject_id, "subject_id")):
            _check_id(value, name)
        check_sink_id(sink)
        granted = _check_fields(fields)
        refused = [f.value for f in granted if not self._policy.allows(f, sink)]
        if refused:
            raise SinkPolicyError(f"the sink policy does not let {sink} resolve {', '.join(refused)}")
        if not _MIN_GRANT_TTL <= ttl <= MAX_GRANT_TTL:
            raise ValueError("a grant lives at least 1 second and at most 10 minutes")
        now = int(self._clock().timestamp())
        claims = {
            "iss": ISSUER,
            "org_id": org_id,
            "run_id": run_id,
            "task_id": task_id,
            "subject_id": subject_id,
            "fields": [f.value for f in granted],
            "sink": sink,
            "iat": now,
            "exp": now + int(ttl.total_seconds()),
            "jti": _b64(secrets.token_bytes(_JTI_BYTES)),
        }
        signing_input = f"{_encode_part({'alg': _ALG, 'kid': self._key.public_key.kid, 'typ': _TYP})}"
        signing_input += f".{_encode_part(claims)}"
        return f"{signing_input}.{_b64(self._key.sign(signing_input.encode('ascii')))}"


class GrantVerifier:
    """The sink's side: checks a grant's header, signature, claims and lifetime. Holds public keys only."""

    def __init__(self, keys: Sequence[GrantVerifyKey], *, clock: Callable[[], datetime] = _utcnow) -> None:
        by_kid = {key.kid: key for key in keys}
        if not keys or len(by_kid) != len(keys):
            raise GrantKeyError("a grant verifier needs at least one verify key, each once")
        self._keys = by_kid
        self._clock = clock

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, clock: Callable[[], datetime] = _utcnow) -> GrantVerifier:
        return cls(verify_keys_from_env(env), clock=clock)

    def verify(self, grant: object) -> Grant:
        """The grant's claims, after the signature, header, claim shapes and lifetime are checked."""
        payload = self._verified_payload(grant)
        claims = _parse_claims(payload)
        now = self._clock()
        if now >= claims.expires_at:
            raise GrantRejected(RejectReason.EXPIRED, f"grant {claims.jti} has expired")
        if claims.issued_at > now + _CLOCK_SKEW:
            raise GrantRejected(RejectReason.MALFORMED, f"grant {claims.jti} is issued in the future")
        return claims

    def _verified_payload(self, grant: object) -> dict[str, Any]:
        if not isinstance(grant, str) or len(grant) > _MAX_GRANT_CHARS or not grant.isascii() or grant.count(".") != 2:
            raise GrantRejected(RejectReason.MALFORMED, "not a compact JWS")
        head, body, signature = grant.split(".")
        header = _json_part(head)
        kid = header.get("kid")
        key = self._keys.get(kid) if isinstance(kid, str) else None
        expected = {"alg": _ALG, "kid": key.kid if key else None, "typ": _TYP}
        if key is None or header != expected:
            raise GrantRejected(RejectReason.BAD_SIGNATURE, "unexpected header or unknown key")
        if not key.verifies(_unb64(signature), f"{head}.{body}".encode("ascii")):
            raise GrantRejected(RejectReason.BAD_SIGNATURE, "signature does not verify")
        return _json_part(body)


def _claim_str(payload: Mapping[str, Any], name: str) -> str:
    value = payload[name]
    try:
        return _check_id(value, name)
    except ValueError:
        raise GrantRejected(RejectReason.MALFORMED, f"claim {name} is malformed") from None


def _claim_time(payload: Mapping[str, Any], name: str) -> datetime:
    value = payload[name]
    if type(value) is not int or not 0 <= value <= _MAX_NUMERIC_DATE:
        raise GrantRejected(RejectReason.MALFORMED, f"claim {name} is not a NumericDate")
    return datetime.fromtimestamp(value, UTC)


def _claim_fields(value: Any) -> frozenset[VaultField]:
    if not isinstance(value, list) or not value or len(set(map(str, value))) != len(value):
        raise GrantRejected(RejectReason.MALFORMED, "claim fields is malformed")
    fields = [_FIELDS_BY_NAME.get(name) if isinstance(name, str) else None for name in value]
    if None in fields:
        raise GrantRejected(RejectReason.MALFORMED, "claim fields names an unknown field")
    return frozenset(f for f in fields if f is not None)


def _parse_claims(payload: Mapping[str, Any]) -> Grant:
    if set(payload) != _CLAIMS or payload["iss"] != ISSUER:
        raise GrantRejected(RejectReason.MALFORMED, "unexpected claim set or issuer")
    jti = payload["jti"]
    if not isinstance(jti, str) or not _JTI_PATTERN.fullmatch(jti):
        raise GrantRejected(RejectReason.MALFORMED, "claim jti is malformed")
    try:
        org_id = TenantContext(payload["org_id"]).org_id
        sink = check_sink_id(payload["sink"])
    except (ValueError, TypeError, SinkPolicyError):
        raise GrantRejected(RejectReason.MALFORMED, "claim org_id or sink is malformed") from None
    issued_at = _claim_time(payload, "iat")
    expires_at = _claim_time(payload, "exp")
    if not timedelta(0) < expires_at - issued_at <= MAX_GRANT_TTL:
        raise GrantRejected(RejectReason.MALFORMED, f"grant {jti} lives longer than 10 minutes")
    return Grant(
        org_id=org_id,
        run_id=_claim_str(payload, "run_id"),
        task_id=_claim_str(payload, "task_id"),
        subject_id=_claim_str(payload, "subject_id"),
        fields=_claim_fields(payload["fields"]),
        sink=sink,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
    )
