"""bcrypt password hashing (PRD §7.8).

bcrypt reads only the first 72 bytes of a password, so longer ones are refused when hashing (two
passwords sharing a 72-byte prefix must not both work) and never verify. Verifying against "no
user" still runs one bcrypt check, so an unknown email takes as long as a wrong password.
"""

from __future__ import annotations

import functools
import secrets

import bcrypt

__all__ = ["MAX_PASSWORD_BYTES", "hash_password", "verify_password"]

MAX_PASSWORD_BYTES = 72
_DUMMY_ROUNDS = 12


def hash_password(password: str, *, rounds: int) -> str:
    raw = password.encode()
    if not raw:
        raise ValueError("a password cannot be empty")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"a password can be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    raw = password.encode()
    if hashed is None or len(raw) > MAX_PASSWORD_BYTES:
        _spend_one_check(raw[:MAX_PASSWORD_BYTES])
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode())
    except ValueError:
        return False


@functools.cache
def _dummy_hash() -> bytes:
    return bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=_DUMMY_ROUNDS))


def _spend_one_check(raw: bytes) -> None:
    bcrypt.checkpw(raw, _dummy_hash())
