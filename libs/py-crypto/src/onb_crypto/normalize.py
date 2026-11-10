"""Canonical forms of sensitive values, shared by display masks and blind indexes.

Each function accepts the layouts people type and returns one canonical string, or raises
``ValueError`` that names the field but never the value.
"""

import re
from typing import Final

_SEPARATORS: Final = re.compile(r"[ -]")
_MOBILE: Final = re.compile(r"(?:\+?91|0)?([6-9][0-9]{9})", re.ASCII)
_EMAIL: Final = re.compile(r"([^@\s]+)@([^@\s]+\.[^@\s]+)")
_PAN: Final = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]", re.ASCII)
_AADHAAR: Final = re.compile(r"[0-9]{12}|[0-9]{4}( |-)[0-9]{4}\1[0-9]{4}", re.ASCII)
_BANK_ACCOUNT: Final = re.compile(r"[0-9]{9,18}", re.ASCII)


def _malformed(field: str) -> ValueError:
    return ValueError(f"malformed {field} value")


def mobile(value: str) -> str:
    """Ten digits, without a ``+91``/``91``/``0`` prefix or separators."""
    match = _MOBILE.fullmatch(_SEPARATORS.sub("", value.strip()))
    if match is None:
        raise _malformed("mobile")
    return match.group(1)


def email_parts(value: str) -> tuple[str, str]:
    """(local part, domain) of a trimmed address, case preserved."""
    match = _EMAIL.fullmatch(value.strip())
    if match is None:
        raise _malformed("email")
    return match.group(1), match.group(2)


def email(value: str) -> str:
    """Trimmed and lower-cased, so one mailbox has one index."""
    local, domain = email_parts(value)
    return f"{local}@{domain}".lower()


def pan(value: str) -> str:
    """Trimmed and upper-cased PAN."""
    canonical = value.strip().upper()
    if _PAN.fullmatch(canonical) is None:
        raise _malformed("pan")
    return canonical


def aadhaar(value: str) -> str:
    """Twelve digits without separators. Shape only: the Verhoeff rule is checked on input (py-guardrails)."""
    trimmed = value.strip()
    if _AADHAAR.fullmatch(trimmed) is None:
        raise _malformed("aadhaar")
    return _SEPARATORS.sub("", trimmed)


def bank_account(value: str) -> str:
    """Nine to eighteen digits."""
    trimmed = value.strip()
    if _BANK_ACCOUNT.fullmatch(trimmed) is None:
        raise _malformed("bank_account")
    return trimmed
