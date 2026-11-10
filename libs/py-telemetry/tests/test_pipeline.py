"""Box 3: a registered processor sees every span before export (the OBS-1 redaction hook)."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence

import pytest
from onb_telemetry import init_telemetry, register_span_processor
from onb_telemetry.pipeline import HookedSpanProcessor
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry, reset_telemetry_globals
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class Journal:
    """One ordered log shared by hooks and the exporter, so 'before export' is checkable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: list[tuple[str, str, int]] = []

    def add(self, who: str, event: str, span_id: int) -> None:
        with self._lock:
            self.entries.append((who, event, span_id))


def _span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return int(span.context.span_id)


class RecordingHook(SpanProcessor):
    def __init__(self, journal: Journal, name: str = "hook") -> None:
        self.journal = journal
        self.name = name
        self.shut_down = False
        self.flushed = False

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self.journal.add(self.name, "start", _span_id(span))

    def on_end(self, span: ReadableSpan) -> None:
        self.journal.add(self.name, "end", _span_id(span))

    def shutdown(self) -> None:
        self.shut_down = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flushed = True
        return True


class JournalExporter(SpanExporter):
    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            self.journal.add("exporter", "export", _span_id(span))
        return SpanExportResult.SUCCESS


class ExplodingHook(SpanProcessor):
    def on_end(self, span: ReadableSpan) -> None:
        raise RuntimeError("redaction failed")


@pytest.fixture
def journal() -> Journal:
    return Journal()


@pytest.fixture
def journalled(journal: Journal) -> Iterator[None]:
    """Telemetry whose export stage writes to the journal (synchronously, like the in-memory one)."""
    with captured_telemetry("pipeline-test") as captured:
        captured.handle.pipeline.set_export_processor(SimpleSpanProcessor(JournalExporter(journal)))
        yield


def _emit(count: int) -> list[int]:
    tracer = trace.get_tracer("test")
    ids = []
    with tracer.start_as_current_span("root") as root:
        ids.append(root.get_span_context().span_id)
        for i in range(count - 1):
            with tracer.start_as_current_span(f"child-{i}") as child:
                ids.append(child.get_span_context().span_id)
    return ids


@pytest.mark.usefixtures("journalled")
def test_registered_processor_sees_every_span_before_export(journal: Journal) -> None:
    register_span_processor(RecordingHook(journal))
    ids = _emit(5)

    seen = [sid for who, event, sid in journal.entries if who == "hook" and event == "end"]
    exported = [sid for who, _, sid in journal.entries if who == "exporter"]
    assert sorted(seen) == sorted(ids)
    assert sorted(exported) == sorted(ids)
    for sid in ids:
        hook_at = journal.entries.index(("hook", "end", sid))
        export_at = journal.entries.index(("exporter", "export", sid))
        assert hook_at < export_at, f"span {sid:x} was exported before the hook saw it"


@pytest.mark.usefixtures("journalled")
def test_spans_from_many_threads_all_pass_the_hook_first(journal: Journal) -> None:
    register_span_processor(RecordingHook(journal))
    ids: list[int] = []
    lock = threading.Lock()

    def work() -> None:
        got = _emit(3)
        with lock:
            ids.extend(got)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 24
    for sid in ids:
        assert journal.entries.index(("hook", "end", sid)) < journal.entries.index(("exporter", "export", sid))


@pytest.mark.usefixtures("journalled")
def test_hooks_run_in_registration_order(journal: Journal) -> None:
    register_span_processor(RecordingHook(journal, "first"))
    register_span_processor(RecordingHook(journal, "second"))
    (sid,) = _emit(1)
    order = [who for who, event, s in journal.entries if s == sid and event in {"end", "export"}]
    assert order == ["first", "second", "exporter"]


@pytest.mark.usefixtures("journalled")
def test_a_failing_hook_stops_the_span_being_exported(journal: Journal) -> None:
    """Fail closed: a redaction hook that raises must not let the unredacted span out."""
    register_span_processor(ExplodingHook())
    _emit(2)
    assert [e for e in journal.entries if e[0] == "exporter"] == []


def test_processors_passed_at_init_see_spans_before_export(journal: Journal) -> None:
    with captured_telemetry("init-hooks", processors=[RecordingHook(journal)]) as captured:
        (sid,) = _emit(1)
        assert ("hook", "end", sid) in journal.entries
        assert [_span_id(s) for s in captured.spans()] == [sid]


def test_shutdown_and_flush_reach_hooks_and_export() -> None:
    journal = Journal()
    hook = RecordingHook(journal)
    export = RecordingHook(journal, "export")
    pipeline = HookedSpanProcessor(export)
    pipeline.add_hook(hook)
    assert pipeline.force_flush(1000) is True
    pipeline.shutdown()
    assert hook.flushed
    assert export.flushed
    assert hook.shut_down
    assert export.shut_down


def test_pipeline_without_an_export_processor_still_flushes_and_shuts_down() -> None:
    hook = RecordingHook(Journal())
    pipeline = HookedSpanProcessor(None)
    pipeline.add_hook(hook)
    assert pipeline.force_flush() is True
    pipeline.shutdown()
    assert hook.shut_down


def test_register_without_init_is_an_error() -> None:
    reset_telemetry_globals()
    with pytest.raises(RuntimeError, match="init_telemetry"):
        register_span_processor(RecordingHook(Journal()))


def test_handle_registers_processors_too(telemetry: CapturedTelemetry, journal: Journal) -> None:
    telemetry.handle.register_span_processor(RecordingHook(journal))
    (sid,) = _emit(1)
    assert ("hook", "end", sid) in journal.entries
    assert len(telemetry.spans()) == 1


def test_exporter_none_still_runs_hooks(journal: Journal) -> None:
    reset_telemetry_globals()
    handle = init_telemetry(
        "no-export", processors=[RecordingHook(journal)], settings=TelemetrySettings(exporter="none"), instrument=False
    )
    try:
        (sid,) = _emit(1)
        assert ("hook", "end", sid) in journal.entries
    finally:
        handle.shutdown()
        reset_telemetry_globals()
