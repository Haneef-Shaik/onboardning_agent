"""Per-field rules for sensitive values (PRD §7.10): validate, then mask and blind-index.

Order matters (PII-3): a value is checked with the input validators first; only a valid value is ever
masked, indexed or sent to the vault. A refusal names the field, never the value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from onb_contracts import ErrorCode, ErrorDetail
from onb_contracts.hires import PiiField
from onb_crypto import BlindIndexField, MaskedField, VaultField, mask
from onb_guardrails.pii import (
    is_valid_aadhaar,
    is_valid_bank_account,
    is_valid_email,
    is_valid_ifsc,
    is_valid_mobile,
    is_valid_pan,
)

from onb_api.errors import AppError

__all__ = ["BLIND_INDEXES", "UNMASKABLE", "invalid", "is_valid", "masked", "vault_field"]

UNMASKABLE: Final = "[masked]"
_MAX_NAME_CHARS: Final = 200


def _is_valid_holder_name(value: str) -> bool:
    return bool(value.strip()) and len(value) <= _MAX_NAME_CHARS and value.isprintable()


_VALIDATORS: Final[Mapping[PiiField, Callable[[str], bool]]] = {
    PiiField.MOBILE: is_valid_mobile,
    PiiField.EMAIL: is_valid_email,
    PiiField.AADHAAR: is_valid_aadhaar,
    PiiField.PAN: is_valid_pan,
    PiiField.BANK_ACCOUNT: is_valid_bank_account,
    PiiField.IFSC: is_valid_ifsc,
    PiiField.ACCOUNT_HOLDER_NAME: _is_valid_holder_name,
}

_MASKS: Final[Mapping[PiiField, MaskedField]] = {
    PiiField.MOBILE: MaskedField.MOBILE,
    PiiField.EMAIL: MaskedField.EMAIL,
    PiiField.AADHAAR: MaskedField.AADHAAR,
    PiiField.PAN: MaskedField.PAN,
    PiiField.BANK_ACCOUNT: MaskedField.BANK_ACCOUNT,
}

# The stored lookups (PRD §7.10 Lookup / dedupe): `email_hash`, `mobile_hash`, `pan_hash`, `aadhaar_hash`.
BLIND_INDEXES: Final[Mapping[PiiField, BlindIndexField]] = {
    PiiField.EMAIL: BlindIndexField.EMAIL,
    PiiField.MOBILE: BlindIndexField.MOBILE,
    PiiField.PAN: BlindIndexField.PAN,
    PiiField.AADHAAR: BlindIndexField.AADHAAR,
}


def is_valid(field: PiiField, value: str) -> bool:
    return _VALIDATORS[field](value)


def invalid(field: PiiField, location: str) -> ErrorDetail:
    return ErrorDetail(field=location, issue=f"Not a valid {field.value}")


def require_valid(field: PiiField, value: str, location: str) -> None:
    if not is_valid(field, value):
        raise AppError(ErrorCode.VALIDATION_ERROR, details=(invalid(field, location),))


def masked(field: PiiField, value: str) -> str:
    """The display form (FR-P1). Only called on a validated value; a field with no mask shows `[masked]`."""
    masked_field = _MASKS.get(field)
    return UNMASKABLE if masked_field is None else mask(masked_field, value)


def vault_field(field: PiiField) -> VaultField:
    return VaultField(field.value)
