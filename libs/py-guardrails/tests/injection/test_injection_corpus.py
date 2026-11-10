"""Injection corpus: every known pattern is detected; the benign-set false-positive rate is measured.

Each line of corpus.jsonl is ``{"id", "text", "pattern"}``. A positive names the pattern id the detector
must report for it (other ids may fire too); a benign line has ``pattern: null`` and any finding on it is
a false positive. The ``neg-hard-*`` lines quote attack phrases in a harmless context: a heuristic
cannot tell them from an attack and is expected to flag them.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from onb_guardrails.injection import PATTERN_IDS, detect_injection

CORPUS_PATH = Path(__file__).with_name("corpus.jsonl")
MIN_KNOWN_PATTERNS = 20
MIN_BENIGN_CASES = 25
# Measured, not wished: changing this set needs a reason in the GR-1 handoff (or its successor's).
KNOWN_FALSE_POSITIVES = frozenset({"neg-hard-security-training"})
MAX_FALSE_POSITIVE_RATE = 0.10


@dataclass(frozen=True)
class Case:
    id: str
    text: str
    pattern: str | None


def _load() -> tuple[Case, ...]:
    rows = (json.loads(line) for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines())
    return tuple(Case(id=row["id"], text=row["text"], pattern=row["pattern"]) for row in rows)


CASES = _load()
POSITIVES = tuple(case for case in CASES if case.pattern is not None)
BENIGN = tuple(case for case in CASES if case.pattern is None)


def test_corpus_covers_at_least_twenty_known_patterns() -> None:
    patterns = {case.pattern for case in POSITIVES}
    assert len(patterns) >= MIN_KNOWN_PATTERNS, sorted(p for p in patterns if p)
    assert patterns <= set(PATTERN_IDS)
    assert len(BENIGN) >= MIN_BENIGN_CASES
    assert len({case.id for case in CASES}) == len(CASES)


def test_every_known_pattern_in_the_corpus_is_detected() -> None:
    missed = [
        (case.id, detect_injection(case.text)) for case in POSITIVES if case.pattern not in detect_injection(case.text)
    ]
    assert missed == []


def test_benign_false_positive_rate_is_measured_and_bounded() -> None:
    flagged = {case.id for case in BENIGN if detect_injection(case.text)}
    rate = len(flagged) / len(BENIGN)
    print(f"benign false-positive rate: {len(flagged)}/{len(BENIGN)} = {rate:.1%}; flagged: {sorted(flagged)}")
    assert flagged == KNOWN_FALSE_POSITIVES
    assert rate <= MAX_FALSE_POSITIVE_RATE
