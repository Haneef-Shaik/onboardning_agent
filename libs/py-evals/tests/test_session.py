"""EVAL-1 box 2 at unit level: the self-test runs the checks of all 27 cases against scripted outputs
through the `fake` connector, k=3, and passes; the negative controls prove the same checks fail bad
behaviour. The same self-test runs against the compose Mongo in `tests_int/test_selftest.py`
(`make evals-selftest`)."""

from __future__ import annotations

import asyncio
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from onb_evals.adapters import register_adapter
from onb_evals.runner import AgentInvocation
from onb_evals.scripted import fake_runtime
from onb_evals.session import (
    SelftestPaths,
    SessionError,
    load_controls,
    load_scripts,
    run_eval,
    run_selftest,
)

from .memory_store import MemoryStore, as_data_store
from .support import REPO
from .test_runner import MemoryDatabases

PATHS = SelftestPaths.in_repo(REPO)


def _selftest(tmp_path: Path, **kwargs: Any) -> Any:
    results = MemoryStore()
    outcome = asyncio.run(
        run_selftest(
            databases=MemoryDatabases(),
            results_store=as_data_store(results),
            results_org="org_platform",
            reports_dir=tmp_path,
            runtime=fake_runtime(),
            **{"paths": PATHS, **kwargs},
        )
    )
    return outcome, results


@pytest.fixture(scope="module")
def selftest(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return _selftest(tmp_path_factory.mktemp("reports"))


def test_the_selftest_runs_all_27_cases_three_times_and_every_check_passes(selftest: Any) -> None:
    outcome, _ = selftest
    report = outcome.report
    assert len(report["cases"]) == 27
    assert all(len(case["repetitions"]) == 3 for case in report["cases"])
    failing = [
        (case["case_id"], check)
        for case in report["cases"]
        for rep in case["repetitions"]
        for check in rep["checks"] + ([{"error": rep["error"]}] if rep["error"] else [])
        if not check.get("passed", False)
    ]
    assert failing == []
    assert report["all_cases_passed"] is True
    assert report["passed"] is True
    assert outcome.passed is True


def test_every_check_type_in_the_dataset_ran(selftest: Any) -> None:
    outcome, _ = selftest
    ran = {check["type"] for case in outcome.report["cases"] for check in case["repetitions"][0]["checks"]}
    assert len(ran) == 29


def test_the_negative_controls_fail_the_checks_they_target(selftest: Any) -> None:
    outcome, _ = selftest
    controls = outcome.report["negative_controls"]
    assert len(controls) == 8
    assert all(control["caught"] for control in controls), controls


def test_the_selftest_writes_a_report_and_an_eval_runs_record(selftest: Any) -> None:
    outcome, results = selftest
    json_path, md_path = outcome.paths
    assert json.loads(json_path.read_text())["run_id"] == outcome.report["run_id"]
    assert md_path.read_text().startswith("# Eval run ")
    [record] = results.data["eval_runs"]
    assert record["_id"] == outcome.report["run_id"]
    assert record["org_id"] == "org_platform"
    assert record["mode"] == "selftest"
    assert len(record["summary"]) == 27


def test_a_case_without_a_script_stops_the_selftest(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts.jsonl"
    lines = PATHS.scripts.read_text().splitlines()[1:]
    scripts.write_text("\n".join(lines) + "\n")
    paths = SelftestPaths(PATHS.dataset, PATHS.fixture, scripts, PATHS.controls)
    with pytest.raises(SessionError, match="no script for"):
        _selftest(tmp_path, paths=paths)


def test_a_control_that_is_not_caught_fails_the_selftest(tmp_path: Path) -> None:
    controls = tmp_path / "controls.jsonl"
    good = load_scripts(PATHS.scripts)["db-002"]
    controls.write_text(json.dumps({"case_id": "db-002", "turns": good, "expect_failed": ["status_in"]}) + "\n")
    paths = SelftestPaths(PATHS.dataset, PATHS.fixture, PATHS.scripts, controls)
    outcome, _ = _selftest(tmp_path, paths=paths, only=["db-002"])
    assert outcome.report["negative_controls"][0]["caught"] is False
    assert outcome.passed is False


def test_script_files_are_validated(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"case_id": "x", "turns": [{"output": {"a": 1}, "content": "both"}]}) + "\n")
    with pytest.raises(SessionError, match="output or content"):
        load_scripts(bad)
    bad.write_text(json.dumps({"case_id": "x", "turns": []}) + "\n" + json.dumps({"case_id": "x", "turns": []}))
    with pytest.raises(SessionError, match="twice"):
        load_scripts(bad)
    bad.write_text(json.dumps({"case_id": "db-002", "turns": [], "expect_failed": []}) + "\n")
    with pytest.raises(SessionError, match="expect_failed"):
        load_controls(bad)


class _AnswersBlocked:
    """A stand-in for a real agent's adapter (AGT-1 registers the DB agent's)."""

    async def invoke(self, invocation: AgentInvocation) -> dict[str, Any]:
        return {"status": "ok", "answer": {"run_ids": ["run_acme_002"]}}


def test_a_full_run_uses_the_registered_adapter_and_the_configured_model(tmp_path: Path) -> None:
    case = next(line for line in PATHS.dataset.read_text().splitlines() if '"id":"db-002"' in line)
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(case.replace('"agent":"db_agent"', '"agent":"session_test_agent"') + "\n")
    register_adapter("session_test_agent", _AnswersBlocked)
    results = MemoryStore()
    outcome = asyncio.run(
        run_eval(
            agent="session_test_agent",
            llm="standard",
            databases=MemoryDatabases(),
            results_store=as_data_store(results),
            results_org="org_platform",
            dataset=dataset,
            fixture=PATHS.fixture,
            reports_dir=tmp_path / "reports",
            runtime=fake_runtime(),
            repeat=3,
            baseline={("session_test_agent", "capability"): Fraction(1)},
        )
    )
    # The stub never calls find, so tool_called fails: capability pass@1 drops from the baseline's 100%.
    assert outcome.report["mode"] == "full"
    assert outcome.report["llm"] == "fake:fake-standard"
    assert {g["name"]: g["passed"] for g in outcome.report["gates"]} == {"capability": False, "regression": False}
    assert outcome.passed is False
    assert results.data["eval_runs"][0]["mode"] == "full"
    assert outcome.paths[0].parent == tmp_path / "reports"


def test_a_full_run_for_an_agent_without_cases_stops(tmp_path: Path) -> None:
    with pytest.raises(SessionError, match="no cases"):
        asyncio.run(
            run_eval(
                agent="nobody",
                llm="standard",
                databases=MemoryDatabases(),
                results_store=as_data_store(MemoryStore()),
                results_org="o",
                dataset=PATHS.dataset,
                fixture=PATHS.fixture,
                reports_dir=tmp_path,
                runtime=fake_runtime(),
            )
        )
