"""PERF-1 (PRD §12 Latency): the statistics and the verdict of `tools/perf/stats.py`. Pure; no stack."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("perf_stats", ROOT / "tools" / "perf" / "stats.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["perf_stats"] = module
    spec.loader.exec_module(module)
    return module


stats = _load()


def test_the_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]  # 1..100
    assert stats.percentile(values, 0.95) == 95.0
    assert stats.percentile(values, 0.50) == 50.0
    assert stats.percentile(values, 0.99) == 99.0
    assert stats.percentile(values, 1.0) == 100.0


def test_the_percentile_of_a_small_sample_is_never_below_the_true_rank() -> None:
    # 20 samples: p95 is the 19th smallest, not an interpolation toward the 20th or the 18th.
    values = [float(v) for v in range(20, 0, -1)]
    assert stats.percentile(values, 0.95) == 19.0


def test_the_percentile_refuses_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="empty"):
        stats.percentile([], 0.95)


def test_a_summary_reports_count_p50_p95_p99_max_in_milliseconds() -> None:
    summary = stats.summarise([0.010] * 95 + [0.200] * 5)
    assert summary.count == 100
    assert summary.p50_ms == 10.0
    assert summary.p95_ms == 10.0
    assert summary.p99_ms == 200.0
    assert summary.max_ms == 200.0


def test_an_endpoint_passes_only_when_its_p95_is_strictly_under_the_limit() -> None:
    under = stats.summarise([0.299] * 100)
    at = stats.summarise([0.300] * 100)
    assert stats.within(under, limit_ms=300.0)
    assert not stats.within(at, limit_ms=300.0)


def test_the_verdict_fails_when_any_endpoint_is_over_and_names_it() -> None:
    results = {
        "GET /me": stats.summarise([0.005] * 50),
        "POST /hires": stats.summarise([0.400] * 50),
    }
    verdict = stats.verdict(results, limit_ms=300.0, min_samples=50)
    assert not verdict.passed
    assert verdict.failing == ("POST /hires",)


def test_the_verdict_fails_an_endpoint_measured_fewer_times_than_required() -> None:
    results = {"GET /me": stats.summarise([0.005] * 49)}
    verdict = stats.verdict(results, limit_ms=300.0, min_samples=50)
    assert not verdict.passed
    assert verdict.failing == ("GET /me",)


def test_the_verdict_passes_when_every_endpoint_is_under_with_enough_samples() -> None:
    results = {"GET /me": stats.summarise([0.005] * 50), "GET /runs": stats.summarise([0.050] * 60)}
    verdict = stats.verdict(results, limit_ms=300.0, min_samples=50)
    assert verdict.passed
    assert verdict.failing == ()


def test_the_verdict_of_nothing_measured_is_a_fail() -> None:
    assert not stats.verdict({}, limit_ms=300.0, min_samples=1).passed
