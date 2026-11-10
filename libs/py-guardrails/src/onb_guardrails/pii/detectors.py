"""Free-text PII detectors and redaction (PRD §7.10 Free-text input).

``detect(text)`` returns typed spans (type, start, end) and never the matched value, so a span can be
logged safely. ``redact(text)`` replaces every span with its typed marker, e.g. ``[PAN]``. Both are pure:
nothing is stored or logged.

Design notes:
- Matching runs on a skeleton of the text (``skeleton.py``): zero-width characters, Unicode spaces,
  full-width forms, non-ASCII digits and Cyrillic/Greek lookalikes hide nothing. Spans map back to the
  original characters.
- Numbers may be grouped the ways people write them: dots, hyphens, slashes, spaces (any Unicode space),
  brackets; Aadhaar groups may mix separators because the Verhoeff gate keeps false positives rare.
- Aadhaar and mobile candidates are found at every start position (overlapping), so a failed candidate
  cannot shadow a real one next to it.
- Overlapping candidates are merged into one span covering their union, so no fragment of a value is
  left behind. The span's type is that of the longest candidate, ties broken by priority: a
  Verhoeff-valid ``91XXXXXXXXXX`` is Aadhaar, not ``91`` + mobile (card trap).
- Every quantifier that can repeat is bounded, so each scan is linear in the text length.
- Every pattern needs a digit or an ``@`` and no marker contains either, so each redaction pass strictly
  removes them and the fix-point loop in ``redact`` terminates.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from onb_guardrails.pii.skeleton import skeleton
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


# Ties between equally long overlapping candidates go to the earlier type here.
_PRIORITY: Final = (
    PiiType.EMAIL,
    PiiType.AADHAAR,
    PiiType.BANK_ACCOUNT,
    PiiType.PAN,
    PiiType.IFSC,
    PiiType.MOBILE,
)
_RANK: Final = {kind: rank for rank, kind in enumerate(_PRIORITY)}

# Any short run of characters that are neither ASCII letters nor digits separates digit groups: spaces,
# dots, hyphens, Unicode minus and dashes, underscores, commas, brackets, " - ". Verhoeff gates Aadhaar.
_NON_ALNUM: Final = r"[^0-9A-Za-z]"
_GAP: Final = rf"{_NON_ALNUM}{{0,3}}"  # between Aadhaar groups
_SEP: Final = rf"{_NON_ALNUM}{{1,3}}"  # between mobile groups
_AADHAAR: Final = re.compile(
    rf"(?<![0-9])(?=([0-9]{{4}}{_GAP}[0-9]{{4}}{_GAP}[0-9]{{4}})(?![0-9]))",
    re.ASCII,
)
_MOBILE_BODY: Final = (
    r"[6-9](?:"
    r"[0-9]{9}"  # 9876543210
    rf"|[0-9]{{4}}{_SEP}[0-9]{{5}}"  # 98765 43210
    rf"|[0-9]{{2}}{_SEP}[0-9]{{3}}{_SEP}[0-9]{{4}}"  # 987 654 3210
    rf"|[0-9]{{3}}{_SEP}[0-9]{{3}}{_SEP}[0-9]{{3}}"  # 9876 543 210
    rf"|[0-9]{{3}}{_SEP}[0-9]{{4}}{_SEP}[0-9]{{2}}"  # 9876 5432 10
    rf"|[0-9]{_SEP}[0-9]{{3}}{_SEP}[0-9]{{2}}{_SEP}[0-9]{{3}}"  # 98 765 43 210
    rf"|[0-9]({_NON_ALNUM})[0-9]{{2}}\2[0-9]{{2}}\2[0-9]{{2}}\2[0-9]{{2}}"  # 98 76 54 32 10 (one separator)
    r")"
)
_MOBILE: Final = re.compile(
    rf"(?<![0-9])(?=((?:(?:(?:00|\+)?91|0){_NON_ALNUM}{{0,3}})?{_MOBILE_BODY})(?![0-9]))",
    re.ASCII,
)
# Compact PAN in any case; an upper-case PAN may also be separated, or glued to the word after it.
_PAN_ANY_CASE: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])", re.ASCII | re.IGNORECASE)
_PAN_UPPER: Final = re.compile(
    rf"(?<![A-Za-z0-9])[A-Z]{{5}}{_NON_ALNUM}{{0,2}}[0-9]{{4}}{_NON_ALNUM}{{0,2}}[A-Z](?![0-9])", re.ASCII
)
_IFSC: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])", re.ASCII | re.IGNORECASE)
# Unicode local parts and domains (IDN); "%40" is a URL-encoded @. Bounded quantifiers keep it linear.
_EMAIL: Final = re.compile(
    r"[\w.%+\-]{1,64}(?:@|%40)(?:[^\W_](?:[\w\-]{0,61}[^\W_])?\.){1,8}[^\W\d_]{2,24}(?![^\W_])",
)
_LOCAL_PART_STOP: Final = frozenset(" <>()[]{}\"'`,;:|\\@/")
# A bare 9-18 digit run is too common to call an account number; it needs an account keyword before it.
# The keyword may follow anything ("_account", "myaccount") and "_" spaces it like whitespace
# ("account_no_123...") and the digits may follow it directly ("account123..."): "\b" treats "_" and
# digits as word characters, which let the digits leak.
_KW_END: Final = r"(?![A-Za-z])"
_KW_GAP: Final = r"[\s_]"
_BANK_ACCOUNT: Final = re.compile(
    rf"(?:a/c|acc(?:oun)?t|acc){_KW_END}\.?"
    rf"(?:{_KW_GAP}{{0,3}}(?:no|number|num){_KW_END}\.?|{_KW_GAP}{{0,3}}\#)?(?:{_KW_GAP}{{1,3}}is)?"
    rf"{_KW_GAP}{{0,3}}[:#\-]?{_KW_GAP}{{0,3}}"
    rf"([0-9](?:[ \-]?[0-9]){{{BANK_ACCOUNT_MIN_DIGITS - 1},{BANK_ACCOUNT_MAX_DIGITS - 1}}})(?![0-9])",
    re.ASCII | re.IGNORECASE,
)


def _email_start(text: str, start: int, floor: int) -> int:
    """Extend an email match left over the rest of its local part (past the 64 characters matched).

    The walk stops at a delimiter, an ``@`` or ``/``, or ``floor`` (the end of the previous email), so the
    walks cover disjoint text and the whole scan stays linear.
    """
    while start > floor and not text[start - 1].isspace() and text[start - 1] not in _LOCAL_PART_STOP:
        start -= 1
    return start


def _candidates(text: str) -> Iterator[PiiSpan]:
    floor = 0
    for m in _EMAIL.finditer(text):
        yield PiiSpan(PiiType.EMAIL, _email_start(text, m.start(), floor), m.end())
        floor = m.end()
    for m in _AADHAAR.finditer(text):
        if aadhaar_digits_are_valid(re.sub(r"[^0-9]", "", m.group(1))):
            yield PiiSpan(PiiType.AADHAAR, m.start(1), m.end(1))
    for m in _BANK_ACCOUNT.finditer(text):
        yield PiiSpan(PiiType.BANK_ACCOUNT, m.start(1), m.end(1))
    for pattern in (_PAN_ANY_CASE, _PAN_UPPER):
        for m in pattern.finditer(text):
            yield PiiSpan(PiiType.PAN, m.start(), m.end())
    for m in _IFSC.finditer(text):
        yield PiiSpan(PiiType.IFSC, m.start(), m.end())
    for m in _MOBILE.finditer(text):
        yield PiiSpan(PiiType.MOBILE, m.start(1), m.end(1))


def _strength(span: PiiSpan) -> tuple[int, int]:
    return span.end - span.start, -_RANK[span.type]


def _merge(candidates: list[PiiSpan]) -> tuple[PiiSpan, ...]:
    """Union of overlapping candidates, typed by the strongest one in each cluster. O(n log n)."""
    merged: list[PiiSpan] = []
    cluster_end = -1
    strongest: PiiSpan | None = None
    cluster_start = 0
    for span in sorted(candidates, key=lambda s: (s.start, -s.end)):
        if strongest is not None and span.start < cluster_end:
            cluster_end = max(cluster_end, span.end)
            strongest = max(strongest, span, key=_strength)
            continue
        if strongest is not None:
            merged.append(PiiSpan(strongest.type, cluster_start, cluster_end))
        strongest, cluster_start, cluster_end = span, span.start, span.end
    if strongest is not None:
        merged.append(PiiSpan(strongest.type, cluster_start, cluster_end))
    return tuple(merged)


def detect(text: str) -> tuple[PiiSpan, ...]:
    """Every sensitive value in ``text`` as non-overlapping typed spans, in text order."""
    skel = skeleton(text)
    spans = _merge(list(_candidates(skel.text)))
    return tuple(PiiSpan(s.type, *skel.original_span(s.start, s.end)) for s in spans)


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
