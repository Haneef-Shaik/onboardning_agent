"""The PII leak scanner behind `make test-pii` (SEC-1; PRD §12 PII, §7.10, §13 "trace with no PII").

It looks for two things in everything it is given:

- **raw fixture values**: each synthetic sensitive value the run was fed (mobile, email, Aadhaar, PAN, bank
  account, IFSC, account-holder name, the text inside a document scan), in the forms people write them
  (digit groups split by spaces, dots or hyphens; any letter case; `%40` for `@`; `+91`/`0` before a mobile).
  A raw hit is never excused.
- **detector hits**: anything PII-3's detectors (`onb_guardrails.pii.detect`) call sensitive, so a value the
  run made up on its own (a model inventing a mobile) is caught too. A detector hit is excused only when it
  sits wholly inside an opaque id (hex ids such as trace, span and record ids, `ptk_` tokens, UUIDs) or
  matches a narrow allow rule that names its reason (`Allow`).

The scanner never copies a value into its result: a hit says where (source and location) and what kind,
never what. Its report is safe to commit.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from onb_guardrails.pii import detect

__all__ = [
    "Allow",
    "Hit",
    "Item",
    "ScanResult",
    "flatten",
    "inside_opaque_id",
    "raw_patterns",
    "scan",
]

# Between two characters of a numeric value people put spaces, dots, hyphens, slashes or brackets.
_DIGIT_GAP: Final = r"[^0-9A-Za-z]{0,3}"
_CODE_GAP: Final = r"[^0-9A-Za-z]{0,2}"
_TOKEN: Final = re.compile(r"[A-Za-z0-9_]+")
# Opaque ids: lowercase hex of 16+ characters (trace/span ids, ObjectIds, `run_`/`h_`/`appr_` + hex), a
# `ptk_` vault token, or a UUID. A detector hit wholly inside one is an id, not a value.
_ID_PREFIXES: Final = ("run", "h", "appr", "job", "ev", "evt", "msg", "conv", "doc", "task", "prop", "chat", "evr", "u")
_HEX_ID: Final = re.compile(rf"(?:(?:{'|'.join(_ID_PREFIXES)})_)*([0-9a-f]{{16,}})")
_PTK: Final = re.compile(r"ptk_[0-9A-Za-z]{20,}")
# A redaction marker glued to letters, digits or `_` replaced digits inside an id (over-redaction, OBS-1/RUN-3).
_GLUED_MARKER: Final = re.compile(
    r"(?<=[A-Za-z0-9_])\[(?:AADHAAR|PAN|IFSC|MOBILE|EMAIL|BANK_ACCOUNT)\]"
    r"|\[(?:AADHAAR|PAN|IFSC|MOBILE|EMAIL|BANK_ACCOUNT)\](?=[A-Za-z0-9_])"
)
_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Item:
    """One string to scan: where it came from (`source`, e.g. `mongo`), and where inside it (`location`,
    e.g. `run_tasks[3].result.output.body`)."""

    source: str
    location: str
    text: str


@dataclass(frozen=True, slots=True)
class Hit:
    """A finding. `kind` is `raw` (a fixture value, named by `label`) or `detector` (a PII-3 type)."""

    kind: str
    source: str
    location: str
    label: str

    def describe(self) -> str:
        return f"{self.kind}:{self.label} in {self.source} at {self.location}"


@dataclass(frozen=True, slots=True)
class Allow:
    """Excuses detector hits (never raw hits) of `pii_type` in `source` at a location matching
    `location`, for a stated `reason`. Keep rules narrow: one collection and field each."""

    source: str
    location: str
    pii_type: str
    reason: str

    def matches(self, hit: Hit) -> bool:
        return (
            hit.kind == "detector"
            and hit.source == self.source
            and hit.label == self.pii_type
            and re.fullmatch(self.location, hit.location) is not None
        )


@dataclass(frozen=True)
class ScanResult:
    hits: tuple[Hit, ...]
    allowed: tuple[tuple[Hit, Allow], ...]
    ids_excused: int
    items_by_source: Mapping[str, int]
    raw_labels: tuple[str, ...]
    allow_rules: tuple[Allow, ...] = field(default=())
    glued_markers: Mapping[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.hits

    def report(self) -> dict[str, Any]:
        """The report as JSON-ready data: counts, hit locations and allow reasons; never a value."""
        allowed = Counter((allow.reason, hit.source, hit.label) for hit, allow in self.allowed)
        return {
            "result": "clean" if self.clean else "LEAK",
            "hits": len(self.hits),
            "raw_values_searched": list(self.raw_labels),
            "items_scanned_by_source": dict(sorted(self.items_by_source.items())),
            "items_scanned": sum(self.items_by_source.values()),
            "detector_hits_inside_opaque_ids": self.ids_excused,
            "markers_glued_into_ids_by_source": dict(sorted(self.glued_markers.items())),
            "detector_hits_allowed": [
                {"source": source, "type": label, "count": count, "reason": reason}
                for (reason, source, label), count in sorted(allowed.items())
            ],
            "findings": [
                {"kind": h.kind, "label": h.label, "source": h.source, "location": h.location} for h in self.hits
            ],
        }


def _numeric(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value)
    return _DIGIT_GAP.join(digits)


def raw_patterns(fixture: Mapping[str, str]) -> dict[str, re.Pattern[str]]:
    """A search pattern per fixture value, matching the forms people write it in (see the module doc)."""
    patterns: dict[str, re.Pattern[str]] = {}
    for label, value in fixture.items():
        if not value.strip():
            raise ValueError(f"fixture value {label!r} is blank")
        compact = re.sub(r"[^0-9A-Za-z]", "", value)
        if compact.isdigit() and len(compact) >= 6:
            prefix = ""
            if label.startswith("mobile"):  # a mobile may carry +91, 0091, 91 or a trunk 0 in front
                compact, prefix = compact[-10:], rf"(?:(?:00|\+)?91{_DIGIT_GAP}|0{_DIGIT_GAP})?"
            patterns[label] = re.compile(rf"(?<![0-9]){prefix}{_numeric(compact)}(?![0-9])")
        elif "@" in value:
            local, _, domain = value.partition("@")
            patterns[label] = re.compile(rf"{re.escape(local)}(?:@|%40){re.escape(domain)}", re.IGNORECASE)
        elif compact.isalnum() and any(c.isdigit() for c in compact):
            body = _CODE_GAP.join(re.escape(c) for c in compact)
            patterns[label] = re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)
        else:
            words = [re.escape(word) for word in value.split()]
            separator = r"(?:\s|_|\+|%20|-|\.|,\s*){1,3}"
            patterns[label] = re.compile(separator.join(words), re.IGNORECASE)
    return patterns


def inside_opaque_id(text: str, start: int, end: int) -> bool:
    """Whether `text[start:end]` lies wholly inside one opaque id (see the module doc)."""
    for uuid in _UUID.finditer(text, max(0, start - 36), min(len(text), end + 36)):
        if uuid.start() <= start and end <= uuid.end():
            return True
    begin = start
    while begin > 0 and (text[begin - 1].isalnum() or text[begin - 1] == "_") and text[begin - 1].isascii():
        begin -= 1
    token = _TOKEN.match(text, begin)
    if token is None or token.start() > start or end > token.end():
        return False
    word = token.group()
    if _PTK.fullmatch(word):
        return True
    hexed = _HEX_ID.fullmatch(word)
    return hexed is not None and re.search(r"[a-f]", hexed.group(1)) is not None


def _leaf(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("latin-1")
    if isinstance(value, bool) or value is None or isinstance(value, datetime | date):
        return None
    if isinstance(value, int | float):
        return str(value)
    return str(value)  # ObjectId, Decimal128, Timestamp…: their text is what a leak would look like


_SAFE_KEY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _segment(key: str, index: int) -> str:
    """A key as a location segment: kept when it is a plain identifier no detector flags, else `<key#i>`,
    so a value stored as a key never reaches a report through its location."""
    if _SAFE_KEY.fullmatch(key) and not detect(key) and not re.search(r"[0-9]{6,}", key):
        return key
    return f"<key#{index}>"


def flatten(source: str, location: str, value: Any) -> Iterator[Item]:
    """Every scalar inside `value` (a document, a list, a JSON text) as an `Item`, keys included: a value
    stored as a key leaks as much as one stored as a value."""
    if isinstance(value, Mapping):
        for index, (key, child) in enumerate(value.items()):
            name = str(key)
            yield Item(source, f"{location}.<key>", name)
            yield from flatten(source, f"{location}.{_segment(name, index)}", child)
        return
    if isinstance(value, list | tuple):
        for index, child in enumerate(value):
            yield from flatten(source, f"{location}[{index}]", child)
        return
    text = _leaf(value)
    if text:
        yield Item(source, location, text)


def flatten_json_lines(source: str, location: str, lines: Iterable[str]) -> Iterator[Item]:
    """Log lines: a JSON line field by field (keys and values: all it holds), any other line as it is."""
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        parsed: Any = None
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
        if isinstance(parsed, dict):
            yield from flatten(source, f"{location}:{number}", parsed)
        else:
            yield Item(source, f"{location}:{number}", line)


def scan(items: Iterable[Item], fixture: Mapping[str, str], allow: Sequence[Allow] = ()) -> ScanResult:
    """Scan `items` for every fixture value and every detector hit."""
    patterns = raw_patterns(fixture)
    hits: list[Hit] = []
    allowed: list[tuple[Hit, Allow]] = []
    ids_excused = 0
    counts: Counter[str] = Counter()
    glued: Counter[str] = Counter()
    for item in items:
        counts[item.source] += 1
        glued[item.source] += len(_GLUED_MARKER.findall(item.text))
        for label, pattern in patterns.items():
            if pattern.search(item.text):
                hits.append(Hit("raw", item.source, item.location, label))
        for span in detect(item.text):
            if inside_opaque_id(item.text, span.start, span.end):
                ids_excused += 1
                continue
            hit = Hit("detector", item.source, item.location, span.type.value)
            rule = next((rule for rule in allow if rule.matches(hit)), None)
            if rule is None:
                hits.append(hit)
            else:
                allowed.append((hit, rule))
    return ScanResult(
        hits=tuple(dict.fromkeys(hits)),
        allowed=tuple(allowed),
        ids_excused=ids_excused,
        items_by_source=dict(counts),
        raw_labels=tuple(patterns),
        allow_rules=tuple(allow),
        glued_markers={source: n for source, n in glued.items() if n},
    )
