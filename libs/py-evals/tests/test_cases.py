"""The case loader (evals/README.md "Case format"): the real dataset loads, bad cases are refused."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from onb_evals.cases import CaseFormatError, load_cases, parse_case

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "evals" / "datasets" / "onboarding_agents_v1.jsonl"

GOOD: dict[str, Any] = {
    "id": "db-900",
    "agent": "db_agent",
    "category": "capability",
    "actor": {"org_id": "org_acme", "role": "SYSTEM"},
    "input": {"message": "hi"},
    "checks": [{"type": "status_in", "values": ["ok"]}],
}


def test_the_v1_dataset_loads_27_cases_13_capability_and_14_safety() -> None:
    cases = load_cases(DATASET)
    assert len(cases) == 27
    categories = [case.category for case in cases]
    assert categories.count("capability") == 13
    assert categories.count("safety") == 14
    assert len({case.id for case in cases}) == 27


def test_check_params_are_typed_and_when_present_is_kept() -> None:
    cases = {case.id: case for case in load_cases(DATASET)}
    check = cases["tb-005"].checks[1]
    assert check.type == "dag_valid"
    assert check.params.when_present is True
    contains = cases["tb-005"].checks[2]
    assert contains.params.model_dump() == {
        "when_present": False,
        "path": "unsupported_requests",
        "value": "jira",
        "case_insensitive": True,
    }


def test_a_good_case_parses_with_its_setup() -> None:
    raw = {**GOOD, "setup": [{"op": "update", "collection": "run_tasks", "filter": {"a": 1}, "set": {"b": 2}}]}
    case = parse_case(raw)
    assert case.setup[0].collection == "run_tasks"
    assert case.actor.org_id == "org_acme"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"checks": [{"type": "llm_judge", "rubric": "be nice"}]}, "unknown check type 'llm_judge'"),
        ({"checks": [{"type": "status_in"}]}, "values"),
        ({"checks": [{"type": "status_in", "values": ["ok"], "extra": 1}]}, "extra"),
        ({"checks": []}, "checks"),
        ({"category": "vibes"}, "category"),
        ({"id": "x 1"}, "id"),
        ({"actor": {"role": "HR"}}, "org_id"),
        ({"setup": [{"op": "drop", "collection": "x", "filter": {}, "set": {}}]}, "op"),
        ({"checks": [{"type": "tool_called"}]}, "tool"),
        ({"checks": [{"type": "final_state", "collection": "c", "filter": {}}]}, "expect"),
        ({"checks": [{"type": "regex_absent", "pattern": "("}]}, "pattern"),
        ({"checks": [{"type": "no_raw_pii", "scope": ["everywhere"]}]}, "scope"),
    ],
)
def test_a_bad_case_is_refused_with_a_clear_message(change: dict[str, Any], message: str) -> None:
    with pytest.raises(CaseFormatError, match=message):
        parse_case({**GOOD, **change})


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(GOOD) + "\n" + json.dumps(GOOD) + "\n")
    with pytest.raises(CaseFormatError, match="duplicate case id 'db-900'"):
        load_cases(path)


def test_a_bad_line_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(GOOD) + "\n\n{not json\n")
    with pytest.raises(CaseFormatError, match="line 3"):
        load_cases(path)


def test_an_empty_dataset_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n")
    with pytest.raises(CaseFormatError, match="no cases"):
        load_cases(path)


def test_cases_filter_by_agent() -> None:
    cases = load_cases(DATASET, agent="run_assistant")
    assert [case.id for case in cases] == [f"ra-00{n}" for n in range(1, 8)]
