"""The hook pipeline as OBS-1 will use it: redact before export, fail closed, never break the caller.

Ended spans are read-only in the SDK, so a redacting hook returns a changed copy (or None to drop
the span); the export stage receives what the last hook returned.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest
from onb_telemetry.pipeline import HookedSpanProcessor, RedactingSpanProcessor, copy_span
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

RAW = "RAW-VALUE-1234"


class MaskingHook(RedactingSpanProcessor):
    """Shaped like OBS-1's processor: rewrite attributes, events and status description."""

    def redact(self, span: ReadableSpan) -> ReadableSpan | None:
        attrs = {k: ("[redacted]" if k == "pan" else v) for k, v in (span.attributes or {}).items()}
        return copy_span(span, attributes=attrs, events=(), status=Status(StatusCode.ERROR, "[redacted]"))


class DroppingHook(RedactingSpanProcessor):
    def redact(self, span: ReadableSpan) -> ReadableSpan | None:
        return None if span.name == "secret" else span


class ListExporter(SpanExporter):
    def __init__(self) -> None:
        self.names: list[str] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.names.extend(s.name for s in spans)
        return SpanExportResult.SUCCESS


def test_a_redacting_hook_changes_what_is_exported(telemetry: CapturedTelemetry) -> None:
    telemetry.handle.register_span_processor(MaskingHook())
    with trace.get_tracer("test").start_as_current_span("task") as span:
        span.set_attribute("pan", RAW)
        span.set_attribute("run_id", "run_1")
        span.add_event("exception", {"exception.message": f"bad {RAW}"})
        span.set_status(Status(StatusCode.ERROR, f"bad {RAW}"))
    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == {"pan": "[redacted]", "run_id": "run_1"}
    assert exported.events == ()
    assert exported.status.description == "[redacted]"
    assert RAW not in exported.to_json()
    assert exported.context == span.get_span_context()  # same identity: the trace stays intact
    assert exported.name == "task"


def test_a_redacting_hook_can_drop_a_span(telemetry: CapturedTelemetry) -> None:
    telemetry.handle.register_span_processor(DroppingHook())
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("secret"):
        pass
    with tracer.start_as_current_span("public"):
        pass
    assert [s.name for s in telemetry.spans()] == ["public"]


def test_later_hooks_see_the_earlier_hooks_redaction(telemetry: CapturedTelemetry) -> None:
    seen: list[object] = []

    class Peek(SpanProcessor):
        def on_end(self, span: ReadableSpan) -> None:
            seen.append((span.attributes or {}).get("pan"))

    telemetry.handle.register_span_processor(MaskingHook())
    telemetry.handle.register_span_processor(Peek())
    with trace.get_tracer("test").start_as_current_span("task") as span:
        span.set_attribute("pan", RAW)
    assert seen == ["[redacted]"]


def test_copy_span_without_changes_is_equal_but_distinct(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("test").start_as_current_span("task", attributes={"a": "1"}):
        pass
    (original,) = telemetry.spans()
    copied = copy_span(original)
    assert copied is not original
    assert copied.to_json() == original.to_json()


def test_copy_span_rejects_unknown_fields(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("test").start_as_current_span("task"):
        pass
    (original,) = telemetry.spans()
    with pytest.raises(TypeError, match="colour"):
        copy_span(original, colour="red")


def test_failure_log_names_no_span_content(telemetry: CapturedTelemetry, caplog: pytest.LogCaptureFixture) -> None:
    class Leaky(SpanProcessor):
        def on_end(self, span: ReadableSpan) -> None:
            raise ValueError(f"cannot parse {dict(span.attributes or {})}")

    telemetry.handle.register_span_processor(Leaky())
    tracer = trace.get_tracer("test")
    with caplog.at_level(logging.ERROR, logger="onb_telemetry"), tracer.start_as_current_span(f"hire {RAW}") as span:
        span.set_attribute("pan", RAW)
    assert telemetry.spans() == ()
    assert "Leaky" in caplog.text
    assert "ValueError" in caplog.text
    assert RAW not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_a_hook_failing_on_start_does_not_break_the_caller_and_the_span_is_dropped(
    telemetry: CapturedTelemetry, caplog: pytest.LogCaptureFixture
) -> None:
    class BadStart(SpanProcessor):
        def on_start(self, span: Span, parent_context: Context | None = None) -> None:
            raise RuntimeError(f"boom {RAW}")

    telemetry.handle.register_span_processor(BadStart())
    tracer = trace.get_tracer("test")
    with caplog.at_level(logging.ERROR, logger="onb_telemetry"), tracer.start_as_current_span("work"):
        pass  # reaching here is the point: the caller is not broken
    with tracer.start_as_current_span("next"):
        pass
    assert telemetry.spans() == ()  # every span whose on_start failed is dropped
    assert "BadStart" in caplog.text
    assert RAW not in caplog.text


def test_spans_ending_after_shutdown_are_never_exported() -> None:
    exporter = ListExporter()
    pipeline = HookedSpanProcessor(SimpleSpanProcessor(exporter))
    provider = TracerProvider()
    provider.add_span_processor(pipeline)
    tracer = provider.get_tracer("t")
    span = tracer.start_span("in-flight")
    pipeline.shutdown()
    span.end()
    assert exporter.names == []


def test_a_failing_hook_shutdown_or_flush_still_reaches_the_export_stage(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(SpanProcessor):
        def shutdown(self) -> None:
            raise RuntimeError("shutdown failed")

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            raise RuntimeError("flush failed")

    class Export(SpanProcessor):
        flushed = False
        shut = False

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            Export.flushed = True
            return True

        def shutdown(self) -> None:
            Export.shut = True

    pipeline = HookedSpanProcessor(Export())
    pipeline.add_hook(Broken())
    with caplog.at_level(logging.ERROR, logger="onb_telemetry"):
        assert pipeline.force_flush() is False
        pipeline.shutdown()
    assert Export.flushed
    assert Export.shut
    assert "Broken" in caplog.text


def test_replacing_the_export_stage_shuts_the_old_one_down() -> None:
    class Old(SpanProcessor):
        shut = False

        def shutdown(self) -> None:
            Old.shut = True

    pipeline = HookedSpanProcessor(Old())
    pipeline.set_export_processor(None)
    assert Old.shut
