"""Raw-PII detection for the hub, shared by the argument check and the response scan (PRD §7.10).

The PRD's detectors (`onb_guardrails.pii`) also match digit runs inside ids and vault tokens, so two
kinds of value are left alone: a vault token (`ptk_…`, exact shape), and an ObjectId-hex or 32-hex id
under an id key (`_id`, `inserted_id`, `*_id`). Anywhere else a hex string is scanned like any text.
Numbers are scanned by their decimal text: a mobile stored as a number is still a mobile.
"""

from __future__ import annotations

import re

from onb_crypto.tokens import is_token
from onb_guardrails.pii import detect, redact

__all__ = ["count_pii", "is_exempt", "redact_text", "scannable"]

_HEX_ID = re.compile(r"[0-9a-f]{24}|[0-9a-f]{32}")


def _id_key(key: str | None) -> bool:
    return key is not None and (key in ("_id", "$oid") or key.endswith("_id"))


def is_exempt(text: str, key: str | None = None) -> bool:
    return is_token(text) or (_id_key(key) and _HEX_ID.fullmatch(text) is not None)


def scannable(value: object) -> str | None:
    """The text to scan for `value`: strings as they are, numbers in decimal; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return format(value, "f") if isinstance(value, float) else str(value)
    return None


def count_pii(text: str, key: str | None = None) -> int:
    return 0 if is_exempt(text, key) else len(detect(text))


def redact_text(text: str) -> str:
    return redact(text)
