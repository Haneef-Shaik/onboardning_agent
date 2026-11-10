"""Opaque PII tokens (PRD §7.10 Tokens): `ptk_` + a ULID + 128 more random bits.

A token says nothing about the value it stands for. The ULID (48-bit millisecond time + 80 random
bits) keeps tokens roughly time-ordered; the extra 128 bits make them unguessable on their own. All
randomness comes from `secrets`. Both parts are Crockford base32, 26 characters each.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Final

__all__ = ["PTK_PREFIX", "TOKEN_LENGTH", "is_token", "new_token"]

PTK_PREFIX: Final = "ptk_"
_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PART_CHARS: Final = 26
TOKEN_LENGTH: Final = len(PTK_PREFIX) + 2 * _PART_CHARS
_ULID_RANDOM_BYTES: Final = 10  # 80 bits
_EXTRA_RANDOM_BYTES: Final = 16  # 128 bits
# 26 base32 characters hold 130 bits; both parts hold 128, so their first character is 0-7.
_SHAPE: Final = re.compile(rf"{PTK_PREFIX}[0-7][{_CROCKFORD}]{{25}}[0-7][{_CROCKFORD}]{{25}}", re.ASCII)


def _base32(value: int, chars: int) -> str:
    out = []
    for _ in range(chars):
        value, digit = divmod(value, 32)
        out.append(_CROCKFORD[digit])
    return "".join(reversed(out))


def new_token(*, now: datetime | None = None) -> str:
    """A fresh token. `now` (timezone-aware) only sets the ULID time; randomness is always `secrets`."""
    moment = datetime.now(UTC) if now is None else now
    if moment.tzinfo is None:
        raise ValueError("token time must be timezone-aware")
    millis = int(moment.timestamp() * 1000)
    ulid_random = int.from_bytes(secrets.token_bytes(_ULID_RANDOM_BYTES), "big")
    extra = int.from_bytes(secrets.token_bytes(_EXTRA_RANDOM_BYTES), "big")
    ulid = (millis << 80) | ulid_random
    return f"{PTK_PREFIX}{_base32(ulid, _PART_CHARS)}{_base32(extra, _PART_CHARS)}"


def is_token(value: object) -> bool:
    """True for a string shaped exactly like a token this module issues."""
    return isinstance(value, str) and _SHAPE.fullmatch(value) is not None
