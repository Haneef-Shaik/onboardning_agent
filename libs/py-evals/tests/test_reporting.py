"""EVAL-1 box 4: a run writes a report to `evals/reports/` and an `eval_runs` record.

Unit level: the report files and their content, the record written through the tenant repository, the
baseline read back from a report, and the throwaway-database lifecycle. The integration test in
`tests_int/test_selftest.py` does the same against the compose Mongo.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from onb_data import tenant_scope
from onb_evals.databases import DATABASE_PREFIX, ThrowawayDatabases
from onb_evals.gates import evaluate_gates
from onb_evals.metrics import CaseResult, CheckOutcome, RepetitionResult
from onb_evals.report import RunInfo, baseline_from_report, build_report, report_stem, write_report
from onb_evals.results import EVAL_RUNS_COLLECTION, record_run

from .memory_store import MemoryStore, as_data_store

NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
INFO = RunInfo(
    run_id="evr_test",
    started_at=NOW,
    finished_at=NOW,
    agent="db_agent",
    llm="fake:eval-scripted",
    dataset="evals/datasets/onboarding_agents_v1.jsonl",
    fixture="seed_v1",
    repeat=3,
    mode="selftest",
)


def _results(fail_one: bool = False) -> tuple[CaseResult, ...]:
    def rep(i: int, ok: bool) -> RepetitionResult:
        check = CheckOutcome("status_in", ok, "" if ok else "status is \"refused\", expected one of ['ok']")
        return RepetitionResult(i, ok, (check,), 2, 10, 5, 12.5)

    ok_case = CaseResult("db-002", "db_agent", "capability", tuple(rep(i, True) for i in range(3)))
    safety = CaseResult("db-003", "db_agent", "safety", tuple(rep(i, not (fail_one and i == 2)) for i in range(3)))
    return ok_case, safety


def _report(fail_one: bool = False) -> dict[str, Any]:
    results = _results(fail_one)
    return build_report(INFO, results, evaluate_gates(results, repeat=3))


def test_the_report_has_metrics_gates_and_every_repetitions_checks() -> None:
    report = _report()
    assert report["run_id"] == "evr_test"
    assert report["passed"] is True
    assert report["metrics"]["db_agent"]["all"]["pass_at_1"] == 1.0
    safety = report["metrics"]["db_agent"]["safety"]
    assert safety["pass_hat_k_exact"] == str(Fraction(1))
    case = report["cases"][1]
    assert case["case_id"] == "db-003"
    assert len(case["repetitions"]) == 3
    assert case["repetitions"][0]["checks"][0]["type"] == "status_in"


def test_a_failed_safety_repetition_makes_the_report_fail_and_names_the_case() -> None:
    report = _report(fail_one=True)
    assert report["passed"] is False
    safety = next(g for g in report["gates"] if g["name"] == "safety")
    assert safety["passed"] is False
    assert "db-003" in safety["detail"]


def test_report_files_are_named_time_agent_model_and_written_as_json_and_markdown(tmp_path: Path) -> None:
    assert report_stem(INFO) == "20261110T093000Z-db_agent-fake_eval-scripted"
    paths = write_report(_report(), INFO, tmp_path / "reports")
    assert [p.name for p in paths] == [
        "20261110T093000Z-db_agent-fake_eval-scripted.json",
        "20261110T093000Z-db_agent-fake_eval-scripted.md",
    ]
    assert json.loads(paths[0].read_text())["run_id"] == "evr_test"
    markdown = paths[1].read_text()
    assert "| db_agent | safety |" in markdown
    assert "PASS" in markdown


def test_the_baseline_is_read_back_exactly_from_a_report(tmp_path: Path) -> None:
    path = write_report(_report(fail_one=True), INFO, tmp_path)[0]
    baseline = baseline_from_report(path)
    assert baseline[("db_agent", "safety")] == Fraction(2, 3)
    assert baseline[("db_agent", "capability")] == Fraction(1)
    assert ("db_agent", "all") not in baseline


def test_the_eval_runs_record_is_written_through_the_tenant_repository() -> None:
    store = MemoryStore()
    record_id = asyncio.run(record_run(as_data_store(store), "org_platform", _report()))
    [record] = store.data[EVAL_RUNS_COLLECTION]
    assert record["_id"] == record_id == "evr_test"
    assert record["org_id"] == "org_platform"
    assert record["passed"] is True
    assert "cases" not in record
    assert record["summary"][0] == {
        "case_id": "db-002",
        "agent": "db_agent",
        "category": "capability",
        "passes": 3,
        "repetitions": 3,
    }
    with tenant_scope("org_other"):
        assert asyncio.run(as_data_store(store).tenant(EVAL_RUNS_COLLECTION).find({})) == []


class _FakeAdmin:
    def __init__(self) -> None:
        self.dropped: list[str] = []

    async def drop_database(self, name: str) -> None:
        self.dropped.append(name)

    async def close(self) -> None:
        return None


def test_each_fresh_database_is_new_and_dropped_even_when_the_block_fails() -> None:
    made: list[str] = []
    admin = _FakeAdmin()

    def connect(uri: str, name: str) -> Any:
        made.append(name)
        return as_data_store(MemoryStore())

    databases = ThrowawayDatabases("mongodb://example", connect=connect, admin=lambda uri: admin)

    async def scenario() -> None:
        async with databases.fresh():
            pass
        with pytest.raises(RuntimeError):
            async with databases.fresh():
                raise RuntimeError("agent blew up")

    asyncio.run(scenario())
    assert len(set(made)) == 2
    assert all(name.startswith(DATABASE_PREFIX) for name in made)
    assert admin.dropped == made


def test_dropping_refuses_a_database_without_the_eval_prefix() -> None:
    databases = ThrowawayDatabases("mongodb://example", admin=lambda uri: _FakeAdmin())
    with pytest.raises(ValueError, match="prefix"):
        asyncio.run(databases.drop("onboarding"))
