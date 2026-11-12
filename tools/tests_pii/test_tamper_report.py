"""SEC-1 box 3: every vault, grant, token and document tamper case, run into one report, is rejected
(PRD §12 "PII integrity": 100% of tampered vault records and forged or widened grants rejected)."""

from __future__ import annotations

import os
from pathlib import Path

from . import tamper

CATEGORIES = {"vault", "grant", "token", "document"}


def test_every_tamper_case_is_rejected(tmp_path: Path) -> None:
    manifest = tamper.Manifest.load()
    assert tamper.guard_problems(manifest) == []
    outcomes = tamper.run_cases(manifest, tmp_path)
    body = tamper.report(manifest, outcomes)
    directory = Path(os.environ.get("PII_SCAN_REPORT_DIR") or tamper.ROOT / "reports" / "test-pii")
    tamper.write(directory, body)
    print(f"\nSEC-1 tamper report: {body['rejected']}/{body['cases']} rejected ({body['rejected_percent']}%)")

    assert set(body["categories"]) == CATEGORIES
    assert all(category["cases"] for category in body["categories"].values())
    assert body["cases"] >= len(manifest.cases())  # parametrized cases expand
    not_rejected = [o.case + ": " + o.detail for o in outcomes if not o.rejected]
    assert not_rejected == []
    assert body["rejected_percent"] == 100.0
