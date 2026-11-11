"""DAG-1 review fixes: conditions the evaluator would silently decide wrong fail the compile instead.

The compiler cannot rely on publish-time validation alone: a `Template(status="published", ...)` can be
built directly. So it refuses, by name, comparisons that would read as false (or true) only because the
types differ, date comparisons with a literal that is not `YYYY-MM-DD` (dates compare as text), and
templates that were never validated (`model_construct`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from onb_agents.dag import CompileError, CompileReason, compile_dag
from onb_contracts.templates import Template

SEED_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"
SENTINEL = "zz-sentinel-4242"


def seed_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    data["variables"] = [*data["variables"], {"name": "level", "type": "number"}, {"name": "remote", "type": "boolean"}]
    return {**data, "version": 1, "status": "published"}


def hire(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-01",
        "manager_id": "u_acme_mgr_ravi",
        "level": 3,
        "remote": False,
    }
    return {**base, **overrides}


def with_when(condition: str) -> Template:
    data = seed_data()
    data["tasks"][3]["when"] = condition  # bitbucket_access
    return Template.model_validate(data)


@pytest.mark.parametrize(
    "condition",
    [
        "role == 1",
        "role != 1",
        "role in [1, 2]",
        "level == 'three'",
        "remote == 1",
        "level == true",
        "role == level",
        "remote != role",
    ],
)
def test_a_comparison_across_types_fails_the_compile(condition: str) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(with_when(condition), hire())
    assert caught.value.reason is CompileReason.BAD_CONDITION
    assert caught.value.task_id == "bitbucket_access"


@pytest.mark.parametrize(
    "condition", ["start_date >= '2026-1-5'", "start_date == '01/12/2026'", "start_date < '2026-13-01'"]
)
def test_a_date_compared_with_a_non_iso_literal_fails_the_compile(condition: str) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(with_when(condition), hire())
    assert caught.value.reason is CompileReason.BAD_CONDITION


@pytest.mark.parametrize(
    ("condition", "skipped"),
    [
        ("start_date >= '2026-11-01'", False),
        ("start_date < '2026-11-01'", True),
        ("level >= 3 and not remote", False),
        ("role in ['sales', 'designer']", True),
        ("role != null", False),
    ],
)
def test_comparisons_of_matching_types_still_decide(condition: str, skipped: bool) -> None:
    task = compile_dag(with_when(condition), hire()).task("bitbucket_access")
    assert (task.status == "skipped") is skipped


def test_an_unvalidated_template_is_refused() -> None:
    template = Template.model_construct(key="engineer-india", version=1, status="published", variables=(), tasks=())
    with pytest.raises(CompileError) as caught:
        compile_dag(template, hire())
    assert caught.value.reason is CompileReason.NOT_PUBLISHED


@pytest.mark.parametrize(
    ("name", "value"),
    [("role", SENTINEL), ("start_date", SENTINEL), ("location", 4242), ("level", SENTINEL), ("remote", SENTINEL)],
)
def test_a_bad_variable_message_never_echoes_the_value(name: str, value: object) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(seed_data()), hire(**{name: value}))
    assert "4242" not in str(caught.value)
    assert "sentinel" not in str(caught.value)


def test_the_hash_names_the_compiler_version() -> None:
    document = compile_dag(Template.model_validate(seed_data()), hire()).document()
    assert document["compiler"] == 1


@pytest.mark.parametrize("condition", ["role", "level", "role or remote"])
def test_a_condition_that_is_not_true_or_false_fails_the_compile(condition: str) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(with_when(condition), hire())
    assert caught.value.reason is CompileReason.BAD_CONDITION
