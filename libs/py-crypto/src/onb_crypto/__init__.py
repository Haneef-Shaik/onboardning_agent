"""PII vault crypto: display masks and blind indexes (PRD §7.10)."""

from onb_crypto.blind_index import MIN_INDEX_KEY_BYTES, BlindIndexField, blind_index
from onb_crypto.masks import (
    MaskedField,
    mask,
    mask_aadhaar,
    mask_bank_account,
    mask_email,
    mask_mobile,
    mask_pan,
)

__all__ = [
    "MIN_INDEX_KEY_BYTES",
    "PROJECT",
    "BlindIndexField",
    "MaskedField",
    "blind_index",
    "mask",
    "mask_aadhaar",
    "mask_bank_account",
    "mask_email",
    "mask_mobile",
    "mask_pan",
]

PROJECT = "py-crypto"
