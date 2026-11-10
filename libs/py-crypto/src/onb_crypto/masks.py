"""Display masks, exactly as PRD §7.10 Display shows them (FR-P1: masked everywhere by default).

PRD examples: mobile ``98XXXXXX21``, email ``j***@domain.com``, Aadhaar ``XXXX XXXX 1234``,
PAN ``ABXXXXX4F``, account ``XXXXXX6789``. The PAN and account masks have a fixed width (the PRD's PAN
mask is 9 characters for a 10-character PAN), so a mask never reveals a value's length.
"""

from enum import StrEnum

from onb_crypto import normalize


class MaskedField(StrEnum):
    MOBILE = "mobile"
    EMAIL = "email"
    AADHAAR = "aadhaar"
    PAN = "pan"
    BANK_ACCOUNT = "bank_account"


def mask_mobile(value: str) -> str:
    digits = normalize.mobile(value)
    return f"{digits[:2]}XXXXXX{digits[-2:]}"


def mask_email(value: str) -> str:
    local, domain = normalize.email_parts(value)
    return f"{local[0]}***@{domain}"


def mask_aadhaar(value: str) -> str:
    return f"XXXX XXXX {normalize.aadhaar(value)[-4:]}"


def mask_pan(value: str) -> str:
    canonical = normalize.pan(value)
    return f"{canonical[:2]}XXXXX{canonical[-2:]}"


def mask_bank_account(value: str) -> str:
    return f"XXXXXX{normalize.bank_account(value)[-4:]}"


_MASKS = {
    MaskedField.MOBILE: mask_mobile,
    MaskedField.EMAIL: mask_email,
    MaskedField.AADHAAR: mask_aadhaar,
    MaskedField.PAN: mask_pan,
    MaskedField.BANK_ACCOUNT: mask_bank_account,
}


def mask(field: MaskedField, value: str) -> str:
    """The display mask of ``value`` for ``field``."""
    return _MASKS[field](value)
