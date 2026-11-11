"""Local tools (`date_calc`, `render`): pure functions the runtime runs in process, no hub involved."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from onb_agents.registry import load_manifests
from onb_agents.runtime.local_tools import LOCAL_TOOLS, date_calc, local_tools_for, render
from pydantic import ValidationError

from .support import ROOT


def call(name: str, args: dict[str, Any]) -> ToolMessage:
    tool = LOCAL_TOOLS[name]
    result = asyncio.run(tool.ainvoke({"type": "tool_call", "name": name, "args": args, "id": "call_1"}))
    assert isinstance(result, ToolMessage)
    return result


def test_date_calc_adds_calendar_days() -> None:
    assert date_calc(date="2026-12-01", days=-7) == {"date": "2026-11-24"}
    assert date_calc(date="2026-12-31", days=1) == {"date": "2027-01-01"}


def test_date_calc_adds_business_days_skipping_weekends() -> None:
    # 2026-12-04 is a Friday: one business day later is Monday the 7th, and back again
    assert date_calc(date="2026-12-04", days=1, business_days=True) == {"date": "2026-12-07"}
    assert date_calc(date="2026-12-07", days=-1, business_days=True) == {"date": "2026-12-04"}
    assert date_calc(date="2026-12-05", days=0, business_days=True) == {"date": "2026-12-05"}


@pytest.mark.parametrize(
    "args",
    [
        {"date": "01-12-2026", "days": 1},
        {"date": "2026-02-30", "days": 1},
        {"date": "2026-12-01", "days": 10_000},
        {"date": "2026-12-01", "days": "3"},
        {"date": "2026-12-01", "days": 1, "extra": True},
    ],
)
def test_date_calc_refuses_bad_arguments(args: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        date_calc(**args)


def test_render_substitutes_declared_variables() -> None:
    variables = {"name": "Priya", "start_date": "2026-12-01"}
    result = render(template="Hi {{ name }}, you start on {{start_date}}.", variables=variables)
    assert result == {"text": "Hi Priya, you start on 2026-12-01."}


def test_render_refuses_an_undeclared_variable_and_never_evaluates_expressions() -> None:
    with pytest.raises(ValueError, match="missing"):
        render(template="Hi {{ name }} {{ surname }}", variables={"name": "Priya"})
    with pytest.raises(ValueError, match="placeholder"):
        render(template="{{ name.__class__ }}", variables={"name": "Priya"})
    with pytest.raises(ValueError, match="placeholder"):
        render(template="{{ 1 + 1 }}", variables={})


def test_render_does_not_render_placeholders_inside_substituted_values() -> None:
    assert render(template="{{ a }}", variables={"a": "{{ b }}", "b": "x"}) == {"text": "{{ b }}"}


def test_a_failing_local_tool_answers_the_model_with_an_error_message() -> None:
    result = call("date_calc", {"date": "not a date", "days": 1})
    assert result.status == "error"
    assert "not a date" not in str(result.content)  # errors never echo the argument


def test_a_local_tool_call_returns_its_json_result() -> None:
    result = call("render", {"template": "Hi {{ n }}", "variables": {"n": "Priya"}})
    assert result.status == "success"
    assert '"text": "Hi Priya"' in str(result.content)


def test_only_the_local_tools_a_manifest_lists_are_given_to_the_agent() -> None:
    manifests = {m.name: m for m in load_manifests(ROOT / "config" / "agents")}
    assert [t.name for t in local_tools_for(manifests["db_agent"])] == ["date_calc"]
    assert [t.name for t in local_tools_for(manifests["templates_agent"])] == ["render"]
    assert local_tools_for(manifests["run_assistant"]) == []


def test_every_local_tool_a_manifest_names_exists() -> None:
    for manifest in load_manifests(ROOT / "config" / "agents"):
        assert set(manifest.tools) <= set(LOCAL_TOOLS), manifest.name


@pytest.mark.parametrize(
    "args",
    [
        {"date": "2026-12-01", "days": "x"},
        {"date": "2026-12-01"},
        {"date": "9999-12-30", "days": 10},
        {"date": "9999-12-30", "days": 10, "business_days": True},
        {"date": "0001-01-01", "days": -1},
    ],
)
def test_bad_arguments_through_the_tool_are_an_error_message_never_a_crash(args: dict[str, Any]) -> None:
    result = call("date_calc", args)
    assert result.status == "error"
    assert str(result.content).startswith("date_calc: ")
    assert "9999" not in str(result.content)


def test_bad_render_arguments_through_the_tool_are_an_error_message() -> None:
    result = call("render", {"template": "", "variables": {"a": {"nested": 1}}})
    assert result.status == "error"
    assert str(result.content).startswith("render: invalid arguments")
