"""Detector corpus: synthetic positives must have zero false negatives; the false-positive rate is measured.

Each line of corpus.jsonl is ``{"id", "text", "pii": [[TYPE, value], ...]}``. A positive's value counts as
found only when one span of that exact type covers it, so redact() removes it entirely. Negatives have
``pii: []``; any span on one is a false positive. The two ``neg-hard-*`` lines are values a regex cannot
tell from PII (a 10-digit order id starting with 9, an IFSC-shaped SKU) and are expected to be flagged.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from onb_guardrails.pii import PiiType, detect, redact

CORPUS_PATH = Path(__file__).with_name("corpus.jsonl")
MIN_CORPUS_SIZE = 50
# Measured, not wished: changing this set needs a reason in the PII-3 handoff (or its successor's).
KNOWN_FALSE_POSITIVES = frozenset({"neg-hard-order-10-digits", "neg-hard-ifsc-shaped-word"})


@dataclass(frozen=True)
class Case:
    id: str
    text: str
    pii: tuple[tuple[PiiType, str], ...]


def _load() -> tuple[Case, ...]:
    cases = []
    for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        pii = tuple((PiiType(kind), value) for kind, value in row["pii"])
        cases.append(Case(id=row["id"], text=row["text"], pii=pii))
    return tuple(cases)


CASES = _load()
POSITIVES = tuple(case for case in CASES if case.pii)
NEGATIVES = tuple(case for case in CASES if not case.pii)


def _missed(case: Case) -> list[tuple[PiiType, str]]:
    spans = detect(case.text)
    missed = []
    for kind, value in case.pii:
        start = case.text.index(value)
        end = start + len(value)
        if not any(s.type is kind and s.start <= start and s.end >= end for s in spans):
            missed.append((kind, value))
    return missed


def test_corpus_has_50_plus_cases_with_unique_ids_and_both_kinds() -> None:
    assert len(CASES) >= MIN_CORPUS_SIZE
    assert len({case.id for case in CASES}) == len(CASES)
    assert len(POSITIVES) >= 25
    assert len(NEGATIVES) >= 25
    assert {kind for case in POSITIVES for kind, _ in case.pii} == set(PiiType)


def test_corpus_has_zero_false_negatives() -> None:
    misses = {case.id: _missed(case) for case in POSITIVES}
    assert {case_id: missed for case_id, missed in misses.items() if missed} == {}


def test_redacting_a_positive_removes_every_expected_value() -> None:
    for case in POSITIVES:
        redacted = redact(case.text)
        for kind, value in case.pii:
            assert value not in redacted, case.id
            assert kind.marker in redacted, case.id


def test_corpus_false_positive_rate_is_measured() -> None:
    flagged = {case.id for case in NEGATIVES if detect(case.text)}
    # The measured rate is evidence for the handoff; run with -s to record it.
    print(
        f"\nPII corpus: {len(CASES)} cases ({len(POSITIVES)} positive, {len(NEGATIVES)} negative); "
        f"false negatives 0; false positives {len(flagged)}/{len(NEGATIVES)} = {len(flagged) / len(NEGATIVES):.1%} "
        f"{sorted(flagged)}"
    )
    assert flagged == KNOWN_FALSE_POSITIVES
