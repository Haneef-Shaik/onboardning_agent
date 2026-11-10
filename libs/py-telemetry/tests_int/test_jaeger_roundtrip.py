"""Box 2: a test FastAPI app's span reaches Jaeger's API through the compose collector (PRD §7.7).

The app runs in this process with `init_telemetry` exporting OTLP/HTTP to the compose collector
(`otel-collector:4318`); the span is then read back from Jaeger's query API (`jaeger:16686`).
"""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote_plus

import fastapi
import pytest
from fastapi.testclient import TestClient
from onb_telemetry import continue_from, init_telemetry, inject_traceparent, register_span_processor
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.testing import captured_telemetry, reset_telemetry_globals
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.trace import SpanKind
from pymongo import MongoClient

from .conftest import Tunnels, required_env

POLL_SECONDS = 30


class ServerSpans(SpanProcessor):
    """A pre-export hook that notes the server spans, so the test knows which trace to look up."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        return

    def on_end(self, span: ReadableSpan) -> None:
        if span.kind is SpanKind.SERVER:
            self.spans.append(span)


@pytest.fixture
def otlp_to_compose(tunnels: Tunnels, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{tunnels.open('otel-collector', 4318)}")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    reset_telemetry_globals()
    yield
    reset_telemetry_globals()


def _jaeger_trace(jaeger: str, trace_id: str) -> dict[str, Any] | None:
    url = f"http://{jaeger}/api/v3/traces/{trace_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body: dict[str, Any] = json.load(response)
            return body
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def _spans(body: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(service.name, span name, span) for every span in an OTLP-JSON Jaeger v3 response."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for resource_spans in body["result"]["resourceSpans"]:
        attrs = {a["key"]: a["value"] for a in resource_spans["resource"]["attributes"]}
        service = attrs["service.name"]["stringValue"]
        for scope in resource_spans["scopeSpans"]:
            found.extend((service, span["name"], span) for span in scope["spans"])
    return found


def _wait_for_trace(jaeger: str, trace_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        body = _jaeger_trace(jaeger, trace_id)
        if body is not None:
            return body
        time.sleep(1)
    pytest.fail(f"trace {trace_id} never reached Jaeger at {jaeger}")


@pytest.mark.usefixtures("otlp_to_compose")
def test_fastapi_span_is_returned_by_jaeger_through_the_collector(tunnels: Tunnels) -> None:
    service = f"f4-probe-{uuid.uuid4().hex[:8]}"
    handle = init_telemetry(service, settings=TelemetrySettings.from_env())
    seen = ServerSpans()
    register_span_processor(seen)

    app = fastapi.FastAPI()  # built after init_telemetry, so auto-instrumented

    @app.get("/ping")
    def ping() -> dict[str, str | None]:
        return {"traceparent": inject_traceparent()}

    response = TestClient(app).get("/ping")
    assert response.status_code == 200
    stored = response.json()["traceparent"]
    with continue_from(stored, "f4.resumed-after-wait"):
        pass
    assert handle.force_flush(10_000)

    (server,) = seen.spans
    assert server.context is not None
    trace_id = f"{server.context.trace_id:032x}"
    body = _wait_for_trace(tunnels.open("jaeger", 16686), trace_id)

    spans = _spans(body)
    names = {(svc, name) for svc, name, _ in spans}
    assert (service, "GET /ping") in names
    assert (service, "f4.resumed-after-wait") in names  # the continuation joined the same trace
    server_json = next(s for svc, name, s in spans if name == "GET /ping")
    assert server_json["traceId"].lower() == trace_id
    print(f"\nJaeger returned trace {trace_id} for service {service}: {sorted(names)}")


@pytest.mark.usefixtures("otlp_to_compose")
def test_jaeger_lists_the_probe_service(tunnels: Tunnels) -> None:
    service = f"f4-svc-{uuid.uuid4().hex[:8]}"
    handle = init_telemetry(service, settings=TelemetrySettings.from_env())
    app = fastapi.FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "yes"}

    TestClient(app).get("/health")
    assert handle.force_flush(10_000)
    jaeger = tunnels.open("jaeger", 16686)
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"http://{jaeger}/api/v3/services", timeout=5) as response:
            if service in json.load(response)["services"]:
                return
        time.sleep(1)
    pytest.fail(f"{service} never listed by Jaeger")


def test_pymongo_commands_get_spans(tunnels: Tunnels) -> None:
    user = quote_plus(required_env("MONGO_ROOT_USERNAME"))
    password = quote_plus(required_env("MONGO_ROOT_PASSWORD"))
    address = tunnels.open("mongo", 27017)
    with captured_telemetry("f4-mongo", instrument=True) as captured:
        client: MongoClient[dict[str, object]] = MongoClient(
            f"mongodb://{user}:{password}@{address}/?authSource=admin&directConnection=true",
            serverSelectionTimeoutMS=10_000,
        )
        try:
            client.admin.command("ping")
        finally:
            client.close()
        mongo_spans = [s for s in captured.spans() if s.kind is SpanKind.CLIENT and "ping" in s.name]
        assert mongo_spans, [s.name for s in captured.spans()]
        attrs = dict(mongo_spans[0].attributes or {})
        assert attrs.get("db.system") == "mongodb" or attrs.get("db.system.name") == "mongodb"
        assert password not in json.dumps(attrs, default=str)
