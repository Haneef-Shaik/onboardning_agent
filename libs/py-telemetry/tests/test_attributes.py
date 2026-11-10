"""Span attribute helpers for the run-level keys named in PRD §7.7."""

from __future__ import annotations

import pytest
from onb_telemetry import attributes as attrs
from onb_telemetry import set_span_attributes, span_attributes
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry import trace


def test_attribute_keys_match_the_prd() -> None:
    assert (attrs.ORG_ID, attrs.RUN_ID, attrs.TASK_ID, attrs.AGENT_NAME, attrs.TOOL_NAME) == (
        "org_id",
        "run_id",
        "task_id",
        "agent.name",
        "tool.name",
    )


def test_span_attributes_skips_unset_values() -> None:
    assert span_attributes(org_id="org_1", run_id="run_1") == {"org_id": "org_1", "run_id": "run_1"}
    assert span_attributes() == {}


def test_span_attributes_names_every_key() -> None:
    assert span_attributes(org_id="o", run_id="r", task_id="t", agent_name="a", tool_name="x") == {
        "org_id": "o",
        "run_id": "r",
        "task_id": "t",
        "agent.name": "a",
        "tool.name": "x",
    }


@pytest.mark.parametrize("bad", ["", "   ", 42])
def test_span_attributes_rejects_blank_or_non_string_values(bad: object) -> None:
    with pytest.raises(ValueError, match="org_id"):
        span_attributes(org_id=bad)  # type: ignore[arg-type]  # the point: a caller passing a non-str


def test_set_span_attributes_on_the_current_span(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("test").start_as_current_span("task"):
        set_span_attributes(org_id="org_1", task_id="task_7", agent_name="it_provisioner")
    (span,) = telemetry.spans()
    assert dict(span.attributes or {}) == {"org_id": "org_1", "task_id": "task_7", "agent.name": "it_provisioner"}


def test_set_span_attributes_on_a_given_span(telemetry: CapturedTelemetry) -> None:
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("outer") as outer, tracer.start_as_current_span("inner"):
        set_span_attributes(outer, tool_name="mongo.find_hire")
    by_name = {s.name: s for s in telemetry.spans()}
    assert dict(by_name["outer"].attributes or {}) == {"tool.name": "mongo.find_hire"}
    assert dict(by_name["inner"].attributes or {}) == {}
