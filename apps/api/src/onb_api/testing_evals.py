"""Test-only: write an `eval_runs` record in the shape the eval harness stores (UI-8).

Apps may not import the harness (`libs/py-evals/tests/test_harness_boundary.py`), so API tests and the
Playwright seed write the record here. `tools/tests_xt` writes runs with the harness's own
`onb_evals.results.record_run` and reads them through the API, and checks this helper writes the same
document, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from onb_data import DataStore, tenant_scope

from onb_api.evals.store import EVAL_RUNS_COLLECTION

__all__ = ["eval_report", "store_eval_run", "stored_eval_run"]

_METRICS = {
    "cases": 2,
    "repetitions": 6,
    "pass_at_1": 0.8333,
    "pass_at_1_exact": "5/6",
    "pass_hat_k": 0.5,
    "pass_hat_k_exact": "1/2",
    "tool_call_accuracy": 1.0,
    "avg_steps": 3.5,
    "p50_latency_ms": 12.0,
    "p95_latency_ms": 30.5,
    "avg_tokens": 410.0,
}


def eval_report(
    run_id: str,
    started: datetime,
    finished: datetime,
    *,
    agent: str = "db_agent",
    passed: bool = True,
    mode: str = "full",
    gate_detail: str = "50.0% >= 50.0%",
    case_passes: tuple[tuple[bool, ...], ...] = ((True, True, True), (True, False, True)),
) -> dict[str, Any]:
    """A harness report (`onb_evals.report.build_report`), with the fields `record_run` stores."""
    return {
        "run_id": run_id,
        "mode": mode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "agent": agent,
        "llm": "standard",
        "dataset": "onboarding_agents_v1.jsonl",
        "fixture": "seed_v1",
        "repeat": 3,
        "passed": passed,
        "all_cases_passed": False,
        "metrics": {agent: {"all": dict(_METRICS), "happy": dict(_METRICS)}},
        "gates": [{"name": "pass_hat_k", "agent": agent, "passed": passed, "detail": gate_detail}],
        "cases": [
            {
                "case_id": f"{agent}-{number:03d}",
                "agent": agent,
                "category": "happy",
                "repetitions": [{"passed": p} for p in passes],
            }
            for number, passes in enumerate(case_passes, start=1)
        ],
    }


def stored_eval_run(report: Mapping[str, Any]) -> dict[str, Any]:
    """The document `onb_evals.results.record_run` stores for `report` (before `org_id` is added)."""
    return {
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
        "summary": [
            {
                "case_id": case["case_id"],
                "agent": case["agent"],
                "category": case["category"],
                "passes": sum(1 for rep in case["repetitions"] if rep["passed"]),
                "repetitions": len(case["repetitions"]),
            }
            for case in report["cases"]
        ],
    }


async def store_eval_run(store: DataStore, org_id: str, report: Mapping[str, Any]) -> None:
    with tenant_scope(org_id):
        await store.tenant(EVAL_RUNS_COLLECTION).insert_one(stored_eval_run(report))
