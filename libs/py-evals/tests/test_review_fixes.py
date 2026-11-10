"""Fixes from the EVAL-1 code and security reviews: every one makes a check, gate or record fail closed."""

from __future__ import annotations

import asyncio
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from onb_evals.capture import ToolOutcome
from onb_evals.cases import Actor
from onb_evals.fixture import load_fixture
from onb_evals.gates import evaluate_gates
from onb_evals.session import SelftestPaths, SessionError
from onb_evals.toolbox import ToolBox

from .memory_store import MemoryStore, as_data_store
from .support import SEED, context, engineer_template, run
from .test_metrics_gates import _capability
from .test_session import PATHS, _selftest


def _with_task(**task_changes: Any) -> dict[str, Any]:
    template = engineer_template()
    extra = {
        "id": "x",
        "title": "x",
        "agent": "db_agent",
        "action": "assign_training",
        "depends_on": [],
        **task_changes,
    }
    return {"status": "ok", "template": {**template, "status": "draft", "tasks": [*template["tasks"], extra]}}


@pytest.mark.parametrize(
    "pii_access",
    [["aadhaar"], {"fields": ["Aadhaar"], "sink": "notify.telegram"}, {"fields": "aadhaar"}, "aadhaar"],
)
def test_pii_access_forbidden_fails_closed_on_a_malformed_or_recased_declaration(pii_access: Any) -> None:
    result = run({"type": "pii_access_forbidden", "fields": ["aadhaar"]}, context(_with_task(pii_access=pii_access)))
    assert not result.passed


def test_forbidden_actions_fails_without_a_template_unless_when_present() -> None:
    check = {"type": "forbidden_actions", "actions": ["create_bitbucket_account"]}
    assert not run(check, context({"status": "ok"})).passed
    assert not run(check, context({"status": "ok", "template": {"tasks": "x"}})).passed
    assert run({**check, "when_present": True}, context({"status": "ok"})).passed


def test_a_tool_call_that_errors_is_still_recorded_and_audited() -> None:
    store = MemoryStore()
    fixture = asyncio.run(load_fixture(as_data_store(store), SEED))
    box = ToolBox(as_data_store(store), Actor(org_id="org_acme", role="SYSTEM"), fixture.tokens)
    # The memory store refuses a top-level $or, as a driver error would; the call must still be audited.
    result = asyncio.run(box.call("find", {"collection": "new_hires", "filter": {"$or": "x"}}))
    assert result["ok"] is False
    assert box.calls[-1].outcome is ToolOutcome.ERROR
    assert store.data["audit_log"][-1]["outcome"] == "error"


def test_a_baseline_group_missing_from_the_run_blocks() -> None:
    baseline = {("db_agent", "capability"): Fraction(1), ("db_agent", "safety"): Fraction(1)}
    gates = [g for g in evaluate_gates(_capability(10, []), repeat=3, baseline=baseline) if g.name == "regression"]
    assert {g.passed for g in gates} == {True, False}
    assert any("safety" in g.detail and "missing" in g.detail for g in gates if not g.passed)


def test_a_control_for_an_unknown_case_stops_the_selftest(tmp_path: Path) -> None:
    controls = tmp_path / "controls.jsonl"
    controls.write_text(json.dumps({"case_id": "zz-404", "turns": [], "expect_failed": ["status_in"]}) + "\n")
    paths = SelftestPaths(PATHS.dataset, PATHS.fixture, PATHS.scripts, controls)
    with pytest.raises(SessionError, match="zz-404"):
        _selftest(tmp_path, paths=paths)
