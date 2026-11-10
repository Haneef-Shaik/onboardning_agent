"""Builds JWS strings with the `cryptography` primitives directly, independent of `onb_crypto.grants`, so
tests can make grants the issuer would refuse (an Aadhaar grant, a 1-hour grant, an extra claim) and
forgeries (another key, `alg: none`, an HMAC grant keyed with the public key)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from onb_crypto import GrantSigningKey

NOW = datetime(2026, 11, 10, 6, 0, 0, tzinfo=UTC)
HIRE_1 = "hire_0001"
HIRE_2 = "hire_0002"
RUN = "run_0001"
TASK = "task_welcome_sms"
TELEGRAM = "notify.telegram"
EMAIL_SINK = "notify.email"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def claims(org_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": "onb.orchestrator",
        "org_id": org_id,
        "run_id": RUN,
        "task_id": TASK,
        "subject_id": HIRE_1,
        "fields": ["mobile"],
        "sink": TELEGRAM,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
        "jti": b64(secrets.token_bytes(16)),
    }
    return {**base, **overrides}


def header_for(key: GrantSigningKey, **overrides: Any) -> dict[str, Any]:
    return {"alg": "EdDSA", "kid": key.public_key.kid, "typ": "onb-pii-grant+jws", **overrides}


def compact(header: dict[str, Any], payload: dict[str, Any]) -> str:
    return ".".join(b64(json.dumps(part, separators=(",", ":")).encode()) for part in (header, payload))


def sign(key: GrantSigningKey, payload: dict[str, Any], *, header: dict[str, Any] | None = None) -> str:
    """A grant signed with `key`'s Ed25519 private key, built without the library's issuer."""
    private = Ed25519PrivateKey.from_private_bytes(key.reveal())
    signing_input = compact(header or header_for(key), payload)
    return f"{signing_input}.{b64(private.sign(signing_input.encode('ascii')))}"


def hmac_forgery(key: GrantSigningKey, payload: dict[str, Any]) -> str:
    """The classic alg-confusion forgery: HS256 keyed with the (public) verify key."""
    header = {"alg": "HS256", "kid": key.public_key.kid, "typ": "onb-pii-grant+jws"}
    signing_input = compact(header, payload)
    mac = hmac.new(key.public_key.raw(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64(mac)}"


def unsigned(key: GrantSigningKey, payload: dict[str, Any]) -> str:
    return compact({"alg": "none", "kid": key.public_key.kid, "typ": "onb-pii-grant+jws"}, payload) + "."
