"""`init_telemetry`: one call gives a service its provider, exporter and auto-instrumentation."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import fastapi
import httpx
import pytest
from fastapi.testclient import TestClient
from onb_telemetry import init_telemetry, instrument_app
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry, reset_telemetry_globals
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import SpanKind

FASTAPI_SCOPE = "opentelemetry.instrumentation.fastapi"


def _scope(span: ReadableSpan) -> str | None:
    return span.instrumentation_scope.name if span.instrumentation_scope else None


@pytest.fixture
def clean_globals() -> Iterator[None]:
    reset_telemetry_globals()
    yield
    reset_telemetry_globals()


@pytest.mark.usefixtures("clean_globals")
def test_init_sets_the_global_provider_with_the_service_name() -> None:
    handle = init_telemetry("svc-a", settings=TelemetrySettings(exporter="none"), instrument=False)
    try:
        assert trace.get_tracer_provider() is handle.provider
        assert handle.provider.resource.attributes["service.name"] == "svc-a"
        assert handle.service_name == "svc-a"
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_init_twice_in_one_process_is_an_error() -> None:
    handle = init_telemetry("svc-a", settings=TelemetrySettings(exporter="none"), instrument=False)
    try:
        with pytest.raises(RuntimeError, match="already"):
            init_telemetry("svc-b", settings=TelemetrySettings(exporter="none"), instrument=False)
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_init_refuses_when_another_provider_owns_the_global() -> None:
    trace.set_tracer_provider(TracerProvider())
    with pytest.raises(RuntimeError, match="global tracer provider"):
        init_telemetry("svc-a", settings=TelemetrySettings(exporter="none"), instrument=False)


@pytest.mark.usefixtures("clean_globals")
@pytest.mark.parametrize("name", ["", "   "])
def test_service_name_is_required(name: str) -> None:
    with pytest.raises(ValueError, match="service_name"):
        init_telemetry(name, settings=TelemetrySettings(exporter="none"), instrument=False)


@pytest.mark.usefixtures("clean_globals")
def test_otlp_exporter_is_batched_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    handle = init_telemetry("svc-otlp", settings=TelemetrySettings(exporter="otlp"), instrument=False)
    try:
        export = handle.pipeline.export_processor
        assert isinstance(export, BatchSpanProcessor)
        assert isinstance(export.span_exporter, OTLPSpanExporter)
        assert export.span_exporter._endpoint == "http://127.0.0.1:9/v1/traces"
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_console_exporter_and_simple_processor() -> None:
    handle = init_telemetry("svc-c", settings=TelemetrySettings(exporter="console"), batch=False, instrument=False)
    try:
        export = handle.pipeline.export_processor
        assert isinstance(export, SimpleSpanProcessor)
        assert isinstance(export.span_exporter, ConsoleSpanExporter)
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_settings_come_from_env_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    handle = init_telemetry("svc-env", instrument=False)
    try:
        assert handle.pipeline.export_processor is None
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_content_capture_is_off_by_default_for_genai_instrumentation(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
        "OPENINFERENCE_HIDE_INPUTS",
        "OPENINFERENCE_HIDE_OUTPUTS",
    ):
        monkeypatch.setenv(var, "something-else")
    handle = init_telemetry("svc-llm", settings=TelemetrySettings(exporter="none"), instrument=False)
    try:
        assert handle.capture_content is False
        assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
        assert os.environ["OPENINFERENCE_HIDE_INPUTS"] == "true"
        assert os.environ["OPENINFERENCE_HIDE_OUTPUTS"] == "true"
    finally:
        handle.shutdown()


@pytest.mark.usefixtures("clean_globals")
def test_content_capture_can_be_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
        "OPENINFERENCE_HIDE_INPUTS",
        "OPENINFERENCE_HIDE_OUTPUTS",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = TelemetrySettings(exporter="none", capture_content=True)
    handle = init_telemetry("svc-llm", settings=settings, instrument=False)
    try:
        assert handle.capture_content is True
        assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "true"
        assert os.environ["OPENINFERENCE_HIDE_INPUTS"] == "false"
        assert os.environ["OPENINFERENCE_HIDE_OUTPUTS"] == "false"
    finally:
        handle.shutdown()


def test_fastapi_app_created_after_init_gets_server_spans() -> None:
    with captured_telemetry("svc-api", instrument=True) as captured:
        app = fastapi.FastAPI()

        @app.get("/ping")
        def ping() -> dict[str, str]:
            return {"ok": "yes"}

        response = TestClient(app).get("/ping")
        assert response.status_code == 200
        server = [s for s in captured.spans() if s.kind is SpanKind.SERVER]
        # FastAPI also has native tracing (scope "fastapi"); the span must come from the instrumentation.
        assert [(s.name, _scope(s)) for s in server] == [("GET /ping", FASTAPI_SCOPE)]
        assert server[0].resource.attributes["service.name"] == "svc-api"


def test_shutdown_removes_the_auto_instrumentation() -> None:
    original = fastapi.FastAPI
    with captured_telemetry("svc-api", instrument=True):
        assert fastapi.FastAPI is not original
    assert fastapi.FastAPI is original


def test_instrument_app_covers_an_app_built_before_init() -> None:
    app = fastapi.FastAPI()  # built before init: the class swap cannot reach it

    @app.get("/early")
    def early() -> dict[str, str]:
        return {"ok": "yes"}

    with captured_telemetry("svc-early", instrument=False) as captured:
        instrument_app(app)
        TestClient(app).get("/early")
        server = [(s.name, _scope(s)) for s in captured.spans() if s.kind is SpanKind.SERVER]
        assert server == [("GET /early", FASTAPI_SCOPE)]


def test_instrument_app_without_init_is_an_error() -> None:
    reset_telemetry_globals()
    with pytest.raises(RuntimeError, match="init_telemetry"):
        instrument_app(fastapi.FastAPI())


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_http() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_httpx_calls_get_client_spans(local_http: str) -> None:
    with captured_telemetry("svc-http", instrument=True) as captured:
        with httpx.Client() as client:
            assert client.get(f"{local_http}/x").status_code == 204
        client_spans = [s for s in captured.spans() if s.kind is SpanKind.CLIENT]
        assert [s.name for s in client_spans] == ["GET"]


def test_pymongo_is_instrumented() -> None:
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

    with captured_telemetry("svc-db", instrument=True) as captured:
        assert PymongoInstrumentor().is_instrumented_by_opentelemetry
        assert "pymongo" in captured.handle.instrumented
    assert not PymongoInstrumentor().is_instrumented_by_opentelemetry


def test_force_flush(telemetry: CapturedTelemetry) -> None:
    assert telemetry.handle.force_flush() is True
