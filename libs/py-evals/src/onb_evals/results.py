"""The `eval_runs` record (PRD §9; read by `GET /evals/runs` for HR_ADMIN, UI-8).

A summary of the report, written through `TenantRepository` under the org that owns the results (the
CLI takes it from `EVAL_RESULTS_ORG_ID`). Per-repetition check details stay in the report file.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from onb_data import DataStore, tenant_scope

__all__ = ["EVAL_RUNS_COLLECTION", "record_run"]

EVAL_RUNS_COLLECTION: Final = "eval_runs"


def _summary(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case["case_id"],
            "agent": case["agent"],
            "category": case["category"],
            "passes": sum(1 for rep in case["repetitions"] if rep["passed"]),
            "repetitions": len(case["repetitions"]),
        }
        for case in report["cases"]
    ]


async def record_run(store: DataStore, org_id: str, report: Mapping[str, Any]) -> str:
    """Insert the run's record and return its id (the report's `run_id`)."""
    record = {
        "_id": report["run_id"],
        "mode": report["mode"],
        "started_at": datetime.fromisoformat(report["started_at"]),
        "finished_at": datetime.fromisoformat(report["finished_at"]),
        "agent": report["agent"],
        "llm": report["llm"],
        "dataset": report["dataset"],
        "fixture": report["fixture"],
        "repeat": report["repeat"],
        "passed": report["passed"],
        "all_cases_passed": report["all_cases_passed"],
        "metrics": report["metrics"],
        "gates": report["gates"],
        "summary": _summary(report),
    }
    with tenant_scope(org_id):
        inserted = await store.tenant(EVAL_RUNS_COLLECTION).insert_one(record)
    return str(inserted)
