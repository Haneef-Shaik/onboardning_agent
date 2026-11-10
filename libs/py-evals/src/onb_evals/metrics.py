"""Run metrics (evals/README.md "Metrics & gates"): pass@1, pass^k, tool-call accuracy, steps, latency, tokens.

Rates are exact `Fraction`s, so a threshold such as 90% is compared without float error; reports turn
them into floats.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

__all__ = ["CaseResult", "CheckOutcome", "Metrics", "RepetitionResult", "compute_metrics", "metrics_by_group"]

ALL = "all"


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    type: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RepetitionResult:
    repetition: int
    passed: bool
    checks: tuple[CheckOutcome, ...]
    steps: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    tool_calls_ok: int = 0
    tool_calls_checked: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    agent: str
    category: str
    repetitions: tuple[RepetitionResult, ...]

    @property
    def pass_rate(self) -> Fraction:
        if not self.repetitions:
            return Fraction(0)
        return Fraction(sum(r.passed for r in self.repetitions), len(self.repetitions))

    @property
    def passed_all(self) -> bool:
        return bool(self.repetitions) and all(r.passed for r in self.repetitions)


@dataclass(frozen=True, slots=True)
class Metrics:
    cases: int
    repetitions: int
    pass_at_1: Fraction
    pass_hat_k: Fraction
    tool_call_accuracy: Fraction | None
    avg_steps: float
    p50_latency_ms: float
    p95_latency_ms: float
    avg_tokens: float


def _percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def compute_metrics(results: Iterable[CaseResult]) -> Metrics:
    cases = list(results)
    reps = [r for case in cases for r in case.repetitions]
    checked = sum(r.tool_calls_checked for r in reps)
    count = len(cases)
    return Metrics(
        cases=count,
        repetitions=len(reps),
        pass_at_1=sum((c.pass_rate for c in cases), Fraction(0)) / count if count else Fraction(0),
        pass_hat_k=Fraction(sum(c.passed_all for c in cases), count) if count else Fraction(0),
        tool_call_accuracy=Fraction(sum(r.tool_calls_ok for r in reps), checked) if checked else None,
        avg_steps=sum(r.steps for r in reps) / len(reps) if reps else 0.0,
        p50_latency_ms=_percentile([r.latency_ms for r in reps], 0.50),
        p95_latency_ms=_percentile([r.latency_ms for r in reps], 0.95),
        avg_tokens=sum(r.input_tokens + r.output_tokens for r in reps) / len(reps) if reps else 0.0,
    )


def metrics_by_group(results: Iterable[CaseResult]) -> dict[tuple[str, str], Metrics]:
    """Metrics per `(agent, category)`, plus `(agent, "all")` for each agent."""
    cases = list(results)
    groups: dict[tuple[str, str], list[CaseResult]] = {}
    for case in cases:
        groups.setdefault((case.agent, case.category), []).append(case)
        groups.setdefault((case.agent, ALL), []).append(case)
    return {key: compute_metrics(members) for key, members in sorted(groups.items())}
