"""Free-text PII detectors and redaction (PRD §7.10 Free-text input).

``detect(text)`` returns typed spans (type, start, end) and never the matched value, so a span can be
logged safely. ``redact(text)`` replaces every span with its typed marker, e.g. ``[PAN]``. Both are pure:
nothing is stored or logged.

Design notes:
- Unicode decimal digits (full-width, Devanagari, ...) are read as ASCII digits before matching, one
  character for one, so span offsets still index the original text and lookalike digits hide nothing.
- Aadhaar and mobile candidates are found at every start position (overlapping), so a failed candidate
  cannot shadow a real one next to it.
- Overlaps resolve longest-first, then by type priority. That is how a Verhoeff-valid ``91XXXXXXXXXX``
  is reported as Aadhaar, not as ``91`` + mobile (card trap).
- Every pattern needs a digit or an ``@`` and no marker contains either, so each redaction pass strictly
  removes them and the fix-point loop in ``redact`` terminates.
"""

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from onb_guardrails.pii.validators import (
    BANK_ACCOUNT_MAX_DIGITS,
    BANK_ACCOUNT_MIN_DIGITS,
    aadhaar_digits_are_valid,
)


class PiiType(StrEnum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    IFSC = "IFSC"
    MOBILE = "MOBILE"
    EMAIL = "EMAIL"
    BANK_ACCOUNT = "BANK_ACCOUNT"

    @property
    def marker(self) -> str:
        """The typed redaction marker, e.g. ``[PAN]``."""
        return f"[{self.value}]"


@dataclass(frozen=True, slots=True)
class PiiSpan:
    """Where a value of ``type`` sits in the text: ``text[start:end]``."""

    type: PiiType
    start: int
    end: int


# Ties between equally long overlapping spans go to the earlier type here.
_PRIORITY: Final = (
    PiiType.EMAIL,
    PiiType.AADHAAR,
    PiiType.BANK_ACCOUNT,
    PiiType.PAN,
    PiiType.IFSC,
    PiiType.MOBILE,
)
_RANK: Final = {kind: rank for rank, kind in enumerate(_PRIORITY)}

_ANY_DECIMAL: Final = re.compile(r"\d")  # Unicode-aware on purpose: finds non-ASCII digits to fold
_A = re.ASCII | re.IGNORECASE

_AADHAAR: Final = re.compile(r"(?<![0-9])(?=([0-9]{4}([ -]?)[0-9]{4}\2[0-9]{4})(?![0-9]))", re.ASCII)
_MOBILE: Final = re.compile(
    r"(?<![0-9])(?=((?:\+91[ -]?|91[ -]?|0)?[6-9][0-9]{4}[ -]?[0-9]{5})(?![0-9]))",
    re.ASCII,
)
_PAN: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])", _A)
_IFSC: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])", _A)
# Bounded quantifiers keep the scan linear: a local part over 64 chars is matched by its last 64.
_EMAIL: Final = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,8}[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9-])",
    re.ASCII,
)
# A bare 9-18 digit run is too common to call an account number; it needs an account keyword before it.
_BANK_ACCOUNT: Final = re.compile(
    r"\b(?:a/c|acc(?:oun)?t|acc)\b\.?"
    r"(?:\s*(?:no|number|num)\b\.?|\s*\#)?(?:\s+is)?\s*[:#-]?\s*"
    rf"([0-9]{{{BANK_ACCOUNT_MIN_DIGITS},{BANK_ACCOUNT_MAX_DIGITS}}})(?![0-9])",
    _A,
)


def _fold_digits(text: str) -> str:
    """Replace each Unicode decimal digit with its ASCII digit; the length is unchanged."""
    return _ANY_DECIMAL.sub(lambda m: str(unicodedata.decimal(m.group())), text)


def _candidates(text: str) -> Iterator[PiiSpan]:
    for m in _EMAIL.finditer(text):
        yield PiiSpan(PiiType.EMAIL, m.start(), m.end())
    for m in _AADHAAR.finditer(text):
        digits = m.group(1).replace(" ", "").replace("-", "")
        if aadhaar_digits_are_valid(digits):
            yield PiiSpan(PiiType.AADHAAR, m.start(1), m.end(1))
    for m in _BANK_ACCOUNT.finditer(text):
        yield PiiSpan(PiiType.BANK_ACCOUNT, m.start(1), m.end(1))
    for m in _PAN.finditer(text):
        yield PiiSpan(PiiType.PAN, m.start(), m.end())
    for m in _IFSC.finditer(text):
        yield PiiSpan(PiiType.IFSC, m.start(), m.end())
    for m in _MOBILE.finditer(text):
        yield PiiSpan(PiiType.MOBILE, m.start(1), m.end(1))


def detect(text: str) -> tuple[PiiSpan, ...]:
    """Every sensitive value in ``text`` as non-overlapping typed spans, in text order."""
    ranked = sorted(
        _candidates(_fold_digits(text)),
        key=lambda s: (s.start - s.end, _RANK[s.type], s.start),
    )
    chosen: list[PiiSpan] = []
    for span in ranked:
        if all(span.end <= kept.start or span.start >= kept.end for kept in chosen):
            chosen.append(span)
    return tuple(sorted(chosen, key=lambda s: s.start))


def _apply(text: str, spans: tuple[PiiSpan, ...]) -> str:
    parts = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(span.type.marker)
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def redact(text: str) -> str:
    """``text`` with every detected value replaced by its typed marker, e.g. ``[PAN]``."""
    result = text
    while spans := detect(result):
        result = _apply(result, spans)
    return result
