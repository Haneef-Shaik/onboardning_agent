"""Whole-value validators for the sensitive fields of PRD §7.10.

The API uses these on input; the free-text detectors reuse the same rules. All are pure and total:
any string in, a bool out, nothing logged.
"""

import re
from typing import Final

from onb_guardrails.pii.verhoeff import verhoeff_is_valid

# 12 digits, either contiguous or as three groups of four with one consistent separator.
_AADHAAR: Final = re.compile(r"[0-9]{12}|[0-9]{4}( |-)[0-9]{4}\1[0-9]{4}", re.ASCII)
_PAN: Final = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]", re.ASCII)
_IFSC: Final = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}", re.ASCII)
# Optional +91 country code; the ten digits contiguous or split 5+5 by one space or hyphen.
_MOBILE: Final = re.compile(r"(?:\+91[ -]?)?[6-9][0-9]{4}[ -]?[0-9]{5}", re.ASCII)
_EMAIL: Final = re.compile(
    r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}",
    re.ASCII,
)
BANK_ACCOUNT_MIN_DIGITS: Final = 9
BANK_ACCOUNT_MAX_DIGITS: Final = 18
_BANK_ACCOUNT: Final = re.compile(rf"[0-9]{{{BANK_ACCOUNT_MIN_DIGITS},{BANK_ACCOUNT_MAX_DIGITS}}}", re.ASCII)


def aadhaar_digits_are_valid(digits: str) -> bool:
    """The Aadhaar rule on bare digits: 12 digits, not starting with 0 or 1, Verhoeff checksum."""
    return len(digits) == 12 and digits[0] not in "01" and verhoeff_is_valid(digits)


def is_valid_aadhaar(value: str) -> bool:
    """12 digits (optionally ``NNNN NNNN NNNN`` or ``NNNN-NNNN-NNNN``), not starting 0/1, Verhoeff valid."""
    if _AADHAAR.fullmatch(value) is None:
        return False
    return aadhaar_digits_are_valid(value.replace(" ", "").replace("-", ""))


def is_valid_pan(value: str) -> bool:
    """PAN in the PRD shape ``[A-Z]{5}[0-9]{4}[A-Z]``."""
    return _PAN.fullmatch(value) is not None


def is_valid_ifsc(value: str) -> bool:
    """IFSC in the PRD shape ``[A-Z]{4}0[A-Z0-9]{6}``."""
    return _IFSC.fullmatch(value) is not None


def is_valid_mobile(value: str) -> bool:
    """Indian mobile ``(+91)?[6-9]`` + 9 digits, allowing a 5+5 split."""
    return _MOBILE.fullmatch(value) is not None


def is_valid_email(value: str) -> bool:
    """A plain ``local@domain.tld`` address."""
    return _EMAIL.fullmatch(value) is not None


def is_valid_bank_account(value: str) -> bool:
    """A bank account number of 9 to 18 digits."""
    return _BANK_ACCOUNT.fullmatch(value) is not None
