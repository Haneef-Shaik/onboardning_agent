"""The API is traced: server spans from the FastAPI instrumentation, the trace id in `meta` (PRD §7.7)."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_telemetry import active_telemetry
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry, reset_telemetry_globals
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanKind, StatusCode

FASTAPI_SCOPE = "opentelemetry.instrumentation.fastapi"


@pytest.fixture
def instrumented() -> Iterator[CapturedTelemetry]:
    with captured_telemetry("api", instrument=True) as captured:
        yield captured


def _server_spans(captured: CapturedTelemetry) -> list[ReadableSpan]:
    return [
        s
        for s in captured.spans()
        if s.kind is SpanKind.SERVER and s.instrumentation_scope and s.instrumentation_scope.name == FASTAPI_SCOPE
    ]


def test_requests_get_server_spans_and_meta_carries_the_trace_id(instrumented: CapturedTelemetry) -> None:
    with TestClient(create_app()) as client:
        body = client.get("/health").json()

    server = _server_spans(instrumented)
    assert len(server) == 1, "expected one server span from the FastAPI instrumentation"
    assert server[0].context is not None
    assert body["meta"]["trace_id"] == f"{server[0].context.trace_id:032x}"


def test_without_telemetry_meta_has_no_trace_id() -> None:
    reset_telemetry_globals()
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["meta"]["trace_id"] is None


def test_an_unhandled_error_marks_the_server_span_as_failed_without_the_message(
    instrumented: CapturedTelemetry,
) -> None:
    app = create_app()

    async def boom() -> None:
        raise RuntimeError("secret-value-in-message")

    app.add_api_route("/test/boom", boom)
    with TestClient(app) as client:
        response = client.get("/test/boom")
    assert response.status_code == 500

    server = _server_spans(instrumented)
    assert len(server) == 1
    span = server[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes.get("error.type") == "RuntimeError"
    assert "secret-value-in-message" not in repr(dict(span.attributes))
    assert "secret-value-in-message" not in (span.status.description or "")
    for event in span.events:
        assert "secret-value-in-message" not in repr(dict(event.attributes or {}))
    assert span.context is not None
    assert response.json()["meta"]["trace_id"] == f"{span.context.trace_id:032x}"


def test_main_initialises_telemetry_as_the_api_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    # main.py also needs the Mongo settings (AUTH-1); the client is lazy, so nothing connects.
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=1")
    monkeypatch.setenv("MONGO_DATABASE", "onb")
    # And the config directory (TPL-2: the template validator's catalog and sink policy).
    monkeypatch.setenv("CONFIG_DIR", str(Path(__file__).resolve().parents[3] / "config"))
    reset_telemetry_globals()
    sys.modules.pop("onb_api.main", None)
    try:
        main = importlib.import_module("onb_api.main")
        assert active_telemetry().service_name == "api"
        # Telemetry started before the Mongo client was made, so the client carries the tracer.
        listeners = [type(listener).__name__ for listener in main.store._client.options.event_listeners]
        assert "CommandTracer" in listeners
        # No `with`: the lifespan would make indexes on a Mongo that is not there.
        assert TestClient(main.app).get("/health").json()["meta"]["trace_id"] is not None
    finally:
        sys.modules.pop("onb_api.main", None)
        reset_telemetry_globals()
