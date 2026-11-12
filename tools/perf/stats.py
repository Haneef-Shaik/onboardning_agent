"""PERF-1 (PRD §12 Latency): percentiles, per-endpoint summaries and the pass/fail verdict.

Pure functions over samples in seconds; `latency.py` collects the samples. Percentiles are nearest-rank
(the value at rank ceil(q·n)), never interpolated, so a p95 is always a latency that was observed and
is never flattered by averaging with a faster neighbour.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Summary", "Verdict", "percentile", "summarise", "verdict", "within"]


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    failing: tuple[str, ...]


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile: the smallest observed value with at least `q` of the sample at or below it."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0.0 < q <= 1.0:
        raise ValueError("q must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(round(q * len(ordered), 9)))
    return ordered[rank - 1]


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


def summarise(samples_s: Sequence[float]) -> Summary:
    return Summary(
        count=len(samples_s),
        p50_ms=_ms(percentile(samples_s, 0.50)),
        p95_ms=_ms(percentile(samples_s, 0.95)),
        p99_ms=_ms(percentile(samples_s, 0.99)),
        max_ms=_ms(max(samples_s)),
        mean_ms=_ms(statistics.fmean(samples_s)),
    )


def within(summary: Summary, *, limit_ms: float) -> bool:
    """The PRD target is strict: p95 < limit."""
    return summary.p95_ms < limit_ms


def verdict(results: Mapping[str, Summary], *, limit_ms: float, min_samples: int) -> Verdict:
    """Every endpoint must have at least `min_samples` samples and a p95 under the limit; nothing measured fails."""
    failing = tuple(
        name
        for name, summary in results.items()
        if summary.count < min_samples or not within(summary, limit_ms=limit_ms)
    )
    return Verdict(passed=bool(results) and not failing, failing=failing)
