"""Synthetic sensitive values for one `make test-pii` session (SEC-1).

Fresh random values every session, each valid by PII-3's validators, so the scan cannot pass by matching a
constant some code happens to avoid, and nothing here is a real person's data (MASTER-PLAN §7).
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from onb_guardrails.pii import (
    is_valid_aadhaar,
    is_valid_bank_account,
    is_valid_email,
    is_valid_ifsc,
    is_valid_mobile,
    is_valid_pan,
    verhoeff_check_digit,
)

_UPPER = string.ascii_uppercase
_LOWER = string.ascii_lowercase


def _digits(count: int, first: str = string.digits) -> str:
    return secrets.choice(first) + "".join(secrets.choice(string.digits) for _ in range(count - 1))


def _letters(count: int, alphabet: str = _UPPER) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(count))


def _aadhaar() -> str:
    body = _digits(11, first="23456789")
    return body + verhoeff_check_digit(body)


@dataclass(frozen=True)
class PiiFixture:
    """One synthetic hire's sensitive values, and a second mobile for the `PUT` that supersedes the first."""

    mobile: str
    mobile_updated: str
    email: str
    aadhaar: str
    pan: str
    bank_account: str
    ifsc: str
    account_holder_name: str
    document_text: str

    @classmethod
    def generate(cls) -> PiiFixture:
        fixture = cls(
            mobile=_digits(10, first="6789"),
            mobile_updated=_digits(10, first="6789"),
            email=f"{_letters(6, _LOWER)}.{_letters(6, _LOWER)}@example.com",
            aadhaar=_aadhaar(),
            pan=_letters(3) + "P" + _letters(1) + _digits(4) + _letters(1),
            bank_account=_digits(12, first="123456789"),
            ifsc=_letters(4) + "0" + _digits(6),
            account_holder_name=f"{_letters(1)}{_letters(7, _LOWER)} {_letters(1)}{_letters(8, _LOWER)}",
            document_text=f"SCANREF{_letters(10)}",
        )
        fixture.check()
        return fixture

    def check(self) -> None:
        checks = {
            "mobile": is_valid_mobile(self.mobile),
            "mobile_updated": is_valid_mobile(self.mobile_updated),
            "email": is_valid_email(self.email),
            "aadhaar": is_valid_aadhaar(self.aadhaar),
            "pan": is_valid_pan(self.pan),
            "bank_account": is_valid_bank_account(self.bank_account),
            "ifsc": is_valid_ifsc(self.ifsc),
        }
        bad = [name for name, ok in checks.items() if not ok]
        if bad:
            raise ValueError(f"generated fixture values are not valid: {bad}")

    def values(self) -> Mapping[str, str]:
        """Every raw value the scan searches for, by label."""
        return MappingProxyType(
            {
                "mobile": self.mobile,
                "mobile_updated": self.mobile_updated,
                "email": self.email,
                "aadhaar": self.aadhaar,
                "pan": self.pan,
                "bank_account": self.bank_account,
                "ifsc": self.ifsc,
                "account_holder_name": self.account_holder_name,
                "document_text": self.document_text,
            }
        )
