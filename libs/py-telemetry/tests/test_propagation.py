"""Box 1: a span continued from a stored `traceparent` stays in the original trace (PRD §5.7, §7.7)."""

from __future__ import annotations

import logging
import re

import pytest
from onb_telemetry import continue_from, inject_traceparent
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry import context as otel_context
from opentelemetry import trace

# W3C trace context: the flags field is one byte (bit 0 sampled, bit 1 random in Level 2).
TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def test_span_continued_from_stored_traceparent_carries_original_trace_id(telemetry: CapturedTelemetry) -> None:
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("run.start") as run_span:
        record = {"run_id": "run_1", "traceparent": inject_traceparent()}
    original = run_span.get_span_context()

    # Later, after an async wait: nothing is current any more, only the stored record remains.
    assert not trace.get_current_span().get_span_context().is_valid
    with continue_from(record["traceparent"], "run.resume") as resumed:
        resumed_ctx = resumed.get_span_context()

    assert resumed_ctx.trace_id == original.trace_id
    assert resumed_ctx.span_id != original.span_id
    exported = {s.name: s for s in telemetry.spans()}
    assert exported["run.resume"].context is not None
    assert exported["run.resume"].context.trace_id == original.trace_id
    assert exported["run.resume"].parent is not None
    assert exported["run.resume"].parent.span_id == original.span_id


def test_inject_traceparent_is_w3c_and_names_current_span(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("test").start_as_current_span("job") as span:
        value = inject_traceparent()
    assert value is not None
    assert TRACEPARENT.match(value)
    ctx = span.get_span_context()
    assert value == f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    assert ctx.trace_flags.sampled


@pytest.mark.usefixtures("telemetry")
def test_inject_traceparent_without_a_current_span_is_none() -> None:
    assert inject_traceparent() is None


@pytest.mark.parametrize("stored", [None, "", "garbage", "00-" + "0" * 32 + "-" + "0" * 16 + "-01"])
def test_missing_or_bad_traceparent_starts_a_new_root_trace(
    telemetry: CapturedTelemetry, stored: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="onb_telemetry"), continue_from(stored, "orphan") as span:
        assert span.get_span_context().is_valid
    (exported,) = telemetry.spans()
    assert exported.parent is None
    if stored:
        assert "traceparent" in caplog.text


@pytest.mark.usefixtures("telemetry")
def test_continue_from_does_not_leak_the_context() -> None:
    with trace.get_tracer("test").start_as_current_span("origin"):
        stored = inject_traceparent()
    before = otel_context.get_current()
    with continue_from(stored, "resume"):
        pass
    assert otel_context.get_current() == before
    assert not trace.get_current_span().get_span_context().is_valid


def test_continue_from_sets_attributes_and_kind(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("test").start_as_current_span("origin"):
        stored = inject_traceparent()
    with continue_from(stored, "resume", attributes={"run_id": "run_1"}, kind=trace.SpanKind.CONSUMER):
        pass
    resume = next(s for s in telemetry.spans() if s.name == "resume")
    assert resume.kind is trace.SpanKind.CONSUMER
    assert dict(resume.attributes or {}) == {"run_id": "run_1"}


@pytest.mark.parametrize("stored", [None, "garbage"])
def test_bad_traceparent_never_adopts_the_ambient_span(telemetry: CapturedTelemetry, stored: str | None) -> None:
    """The stored record decides the parent: a damaged one starts a new trace, even inside a span."""
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("ambient") as ambient, continue_from(stored, "orphan") as orphan:
        assert orphan.get_span_context().trace_id != ambient.get_span_context().trace_id
    by_name = {s.name: s for s in telemetry.spans()}
    assert by_name["orphan"].parent is None
