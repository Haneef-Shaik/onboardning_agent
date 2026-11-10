"""bcrypt password hashing (PRD §7.8).

bcrypt reads only the first 72 bytes of a password, so longer ones are refused when hashing (two
passwords sharing a 72-byte prefix must not both work) and never verify. Verifying against "no
user" still runs one bcrypt check at the configured cost, so an unknown email takes as long as a
wrong password. Both functions block for the bcrypt cost: call them from a thread (`run_in_threadpool`)
in async code, never on the event loop.
"""

from __future__ import annotations

import functools
import secrets

import bcrypt

__all__ = ["MAX_PASSWORD_BYTES", "hash_password", "verify_password", "warm_up"]

MAX_PASSWORD_BYTES = 72


def hash_password(password: str, *, rounds: int) -> str:
    raw = password.encode()
    if not raw:
        raise ValueError("a password cannot be empty")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"a password can be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, hashed: str | None, *, rounds: int) -> bool:
    """`rounds` is the configured cost, used for the stand-in check when there is no usable hash."""
    raw = password.encode()
    if hashed is None or len(raw) > MAX_PASSWORD_BYTES:
        bcrypt.checkpw(raw[:MAX_PASSWORD_BYTES], _dummy_hash(rounds))
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode())
    except ValueError:
        return False


@functools.cache
def _dummy_hash(rounds: int) -> bytes:
    return bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=rounds))


def warm_up(rounds: int) -> None:
    """Make the stand-in hash at startup, so the first unknown-email login is not slower."""
    _dummy_hash(rounds)
