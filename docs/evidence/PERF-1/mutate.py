"""PERF-1 mutation checks: break each guard of tools/perf on purpose, run its tests, restore the source.

Run from the repo root: `uv run --frozen python docs/evidence/PERF-1/mutate.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

TESTS = ["tools/tests/test_perf_stats.py", "tools/tests/test_perf_latency.py"]
MUTATIONS = [
    (
        "M1 p95 limit not strict (< -> <=)",
        "tools/perf/stats.py",
        "return summary.p95_ms < limit_ms",
        "return summary.p95_ms <= limit_ms",
    ),
    (
        "M2 min sample count ignored",
        "tools/perf/stats.py",
        "if summary.count < min_samples or not within",
        "if not within",
    ),
    (
        "M3 nothing measured passes",
        "tools/perf/stats.py",
        "passed=bool(results) and not failing",
        "passed=not failing",
    ),
    (
        "M4 percentile rank lowered (flatters the p95)",
        "tools/perf/stats.py",
        "rank = max(1, math.ceil(round(q * len(ordered), 9)))",
        "rank = max(1, int(q * len(ordered)) - 1)",
    ),
    (
        "M5 unexpected status not checked",
        "tools/perf/latency.py",
        "if response.status_code != expect:",
        "if False:",
    ),
    (
        "M6 resumed counts a ready (not started) task",
        "tools/perf/latency.py",
        'STARTED = frozenset({"running", "done"})',
        'STARTED = frozenset({"running", "done", "ready"})',
    ),
    (
        "M7 resumed ignores the dependency on the approval",
        "tools/perf/latency.py",
        'APPROVAL_TASK in t["depends_on"] and t["status"] in STARTED',
        't["status"] in STARTED',
    ),
    (
        "M8 pause not kept before resume",
        "tools/perf/latency.py",
        'rest[at:at] = ["POST /runs/{run_id}/pause", "POST /runs/{run_id}/resume"]',
        'rest[at:at] = ["POST /runs/{run_id}/resume", "POST /runs/{run_id}/pause"]',
    ),
    (
        "M9 the 30-decision floor removed",
        "tools/perf/latency.py",
        "if args.decisions < MIN_DECISIONS:",
        "if False:",
    ),
    (
        "M10 a non-local host allowed",
        "tools/perf/latency.py",
        'if urlparse(base_url).hostname not in {"localhost", "127.0.0.1"}:',
        "if False:",
    ),
]


def main() -> None:
    killed = 0
    for name, path, old, new in MUTATIONS:
        source_file = Path(path)
        source = source_file.read_text()
        if source.count(old) != 1:
            raise SystemExit(f"{name}: the guard text is not found exactly once in {path}")
        source_file.write_text(source.replace(old, new))
        try:
            out = subprocess.run(
                ["uv", "run", "--frozen", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            source_file.write_text(source)
        was_killed = out.returncode != 0
        killed += was_killed
        print(f"{name}: {'KILLED' if was_killed else 'SURVIVED'}")
        for line in [x for x in out.stdout.splitlines() if x.startswith("FAILED")][:3]:
            print(f"    {line[:220]}")
    print(f"{killed} of {len(MUTATIONS)} killed")
    diff = subprocess.run(["git", "diff", "--stat", "--", "tools"], capture_output=True, text=True, check=False)
    print("source diff after all mutations:", diff.stdout.strip() or "0 files differ")


if __name__ == "__main__":
    main()
