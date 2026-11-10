"""In-memory telemetry for tests. Enable the fixture in a conftest: `from onb_telemetry.testing import telemetry`.

The `telemetry` fixture runs `init_telemetry` with an in-memory exporter behind a synchronous
processor, so every span is readable as soon as it ends. Test-only: it resets process globals.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.test.globals_test import reset_trace_globals

from onb_telemetry import setup
from onb_telemetry.settings import TelemetrySettings


@dataclass(frozen=True)
class CapturedTelemetry:
    handle: setup.TelemetryHandle
    exporter: InMemorySpanExporter

    def spans(self) -> tuple[ReadableSpan, ...]:
        return self.exporter.get_finished_spans()

    def clear(self) -> None:
        self.exporter.clear()


def reset_telemetry_globals() -> None:
    """Forget any provider and handle, as if this process had never called `init_telemetry`."""
    active = setup._active
    if active is not None:
        active.shutdown()
    setup.uninstall_log_redaction_for_tests()
    reset_trace_globals()
    _forget_pymongo_tracer()


def _forget_pymongo_tracer() -> None:
    """PymongoInstrumentor builds its command listener once per process, bound to the provider of
    the first `instrument()`, and only disables it on `uninstrument()`. A service initialises once,
    so that is fine there; tests re-initialise, so drop the cached listener and let the next
    `instrument()` register a fresh one bound to the new provider (the old one stays disabled)."""
    module = importlib.import_module("opentelemetry.instrumentation.pymongo")
    module.PymongoInstrumentor()._commandtracer_instance = None  # instrumentors are singletons


@contextmanager
def captured_telemetry(
    service_name: str = "test-service",
    *,
    processors: Sequence[SpanProcessor] = (),
    instrument: bool = False,
) -> Iterator[CapturedTelemetry]:
    reset_telemetry_globals()
    exporter = InMemorySpanExporter()
    handle = setup.init_telemetry(
        service_name,
        processors=processors,
        exporter=exporter,
        settings=TelemetrySettings(exporter="none"),
        batch=False,
        instrument=instrument,
    )
    try:
        yield CapturedTelemetry(handle, exporter)
    finally:
        reset_telemetry_globals()


@pytest.fixture
def telemetry() -> Iterator[CapturedTelemetry]:
    with captured_telemetry() as captured:
        yield captured
