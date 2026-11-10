"""`init_telemetry(service_name)`: the one call every Python service makes at startup (PRD §7.7).

It builds the tracer provider with the service's resource, puts the hook pipeline in front of the
exporter, makes it the global provider, and turns on auto-instrumentation for FastAPI, httpx and
PyMongo (each only if that library is installed).

FastAPI instrumentation swaps the `fastapi.FastAPI` class, so it reaches apps built after this call.
For an app built earlier, call `instrument_app(app)`.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter

from onb_telemetry.log_scrub import LogRedaction, install_log_redaction
from onb_telemetry.pipeline import HookedSpanProcessor
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.span_scrub import PiiScrubbingSpanProcessor

# (library to look for, instrumentation module, instrumentor class). Each runs with our provider.
_INSTRUMENTATIONS: tuple[tuple[str, str, str], ...] = (
    ("fastapi", "opentelemetry.instrumentation.fastapi", "FastAPIInstrumentor"),
    ("httpx", "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
    ("pymongo", "opentelemetry.instrumentation.pymongo", "PymongoInstrumentor"),
)

# The switches GenAI instrumentations read for prompt/completion content (LLM-1 adds OpenInference),
# each mapped to whether "true" means "capture".
_CONTENT_SWITCHES: dict[str, bool] = {
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": True,
    "OPENINFERENCE_HIDE_INPUTS": False,
    "OPENINFERENCE_HIDE_OUTPUTS": False,
}

_log = logging.getLogger("onb_telemetry")
_lock = threading.Lock()
_active: TelemetryHandle | None = None


@dataclass(frozen=True)
class TelemetryHandle:
    """What `init_telemetry` set up; `shutdown()` flushes, stops export and removes instrumentation.

    Add span processors only with `register_span_processor`: one added straight to `provider`
    (`provider.add_span_processor`) runs after export and bypasses the redaction hooks.
    OTel lets a process set its global provider once, so after `shutdown()` a service cannot
    initialise telemetry again (the test helpers in `onb_telemetry.testing` can).
    """

    service_name: str
    provider: TracerProvider
    pipeline: HookedSpanProcessor
    capture_content: bool
    instrumented: tuple[str, ...]
    log_redaction: LogRedaction

    def register_span_processor(self, processor: SpanProcessor) -> None:
        """Add a hook that sees every later span before it is exported (OBS-1's redaction)."""
        self.pipeline.add_hook(processor)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.provider.force_flush(timeout_millis)

    def shutdown(self) -> None:
        global _active
        try:
            for library in self.instrumented:
                _uninstrument(library)
            self.provider.shutdown()
        finally:
            self.log_redaction.uninstall()
            with _lock:
                if _active is self:
                    _active = None


def init_telemetry(
    service_name: str,
    *,
    processors: Sequence[SpanProcessor] = (),
    exporter: SpanExporter | None = None,
    settings: TelemetrySettings | None = None,
    batch: bool = True,
    instrument: bool = True,
) -> TelemetryHandle:
    """Set up tracing for this process. Call once, at startup, before building the FastAPI app.

    PII scrubbing is always on (PRD §7.7): spans pass `PiiScrubbingSpanProcessor` after every hook and
    before export, and log records are scrubbed (`install_log_redaction`). Neither can be turned off.
    `processors` are hooks run before the scrubber, in order. `exporter` overrides the one the settings
    choose (tests pass an in-memory exporter). `settings` default to `TelemetrySettings.from_env()`.
    """
    global _active
    if not service_name.strip():
        raise ValueError("service_name must be a non-blank string")
    config = settings if settings is not None else TelemetrySettings.from_env()
    with _lock:
        if _active is not None:
            raise RuntimeError(f"telemetry is already initialised for {_active.service_name!r} in this process")
        span_exporter = exporter if exporter is not None else _exporter_for(config)
        export = _export_processor(span_exporter, batch=batch)
        pipeline = HookedSpanProcessor(export, final_hook=PiiScrubbingSpanProcessor())
        for processor in processors:
            pipeline.add_hook(processor)
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        provider.add_span_processor(pipeline)
        _install_global(provider)
        _apply_content_capture(config.capture_content)
        log_redaction = install_log_redaction()
        try:
            instrumented = _instrument_libraries(provider) if instrument else ()
        except Exception:
            # The global provider cannot be unset; shut it down so nothing exports half-configured.
            _uninstrument_all()
            provider.shutdown()
            log_redaction.uninstall()
            raise
        _active = TelemetryHandle(service_name, provider, pipeline, config.capture_content, instrumented, log_redaction)
        return _active


def active_telemetry() -> TelemetryHandle:
    """The handle `init_telemetry` returned; raises if telemetry is not initialised."""
    handle = _active
    if handle is None:
        raise RuntimeError("telemetry is not initialised: call init_telemetry(service_name) at startup")
    return handle


def register_span_processor(processor: SpanProcessor) -> None:
    """Add a hook to the active pipeline; it sees every later span before export."""
    active_telemetry().register_span_processor(processor)


def instrument_app(app: Any) -> None:
    """Instrument a FastAPI app built before `init_telemetry` (safe to call twice)."""
    handle = active_telemetry()
    module = importlib.import_module("opentelemetry.instrumentation.fastapi")
    module.FastAPIInstrumentor.instrument_app(app, tracer_provider=handle.provider)


def _exporter_for(settings: TelemetrySettings) -> SpanExporter | None:
    if settings.exporter == "otlp":
        return OTLPSpanExporter()  # endpoint, headers and timeout come from the OTEL_EXPORTER_OTLP_* env
    if settings.exporter == "console":
        return ConsoleSpanExporter()
    return None


def _export_processor(exporter: SpanExporter | None, *, batch: bool) -> SpanProcessor | None:
    if exporter is None:
        return None
    return BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)


def _install_global(provider: TracerProvider) -> None:
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is not provider:
        provider.shutdown()
        raise RuntimeError("a global tracer provider was already set in this process; init_telemetry must own it")


def _apply_content_capture(capture: bool) -> None:
    # One switch for every GenAI instrumentation, so none of them can capture content on its own.
    for var, true_means_capture in _CONTENT_SWITCHES.items():
        os.environ[var] = "true" if capture == true_means_capture else "false"


def _instrumentor(library: str) -> Any:
    _, module_name, class_name = next(entry for entry in _INSTRUMENTATIONS if entry[0] == library)
    return getattr(importlib.import_module(module_name), class_name)()


def _uninstrument(library: str) -> None:
    try:
        instrumentor = _instrumentor(library)
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()
    except Exception as exc:  # keep going: the remaining libraries and the provider still shut down
        _log.error("could not remove %s instrumentation: %s", library, type(exc).__name__)


def _uninstrument_all() -> None:
    for library, _, _ in _INSTRUMENTATIONS:
        if importlib.util.find_spec(library) is not None:
            _uninstrument(library)


def _instrument_libraries(provider: TracerProvider) -> tuple[str, ...]:
    done = []
    for library, _, _ in _INSTRUMENTATIONS:
        if importlib.util.find_spec(library) is None:
            continue
        instrumentor = _instrumentor(library)
        if instrumentor.is_instrumented_by_opentelemetry:
            continue  # someone else instrumented it; leave theirs alone
        instrumentor.instrument(tracer_provider=provider)
        done.append(library)
    return tuple(done)
