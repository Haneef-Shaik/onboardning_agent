"""Env-driven configuration: standard OTEL_* variables, content capture off by default (PRD §7.7)."""

from __future__ import annotations

import pytest
from onb_telemetry.settings import TelemetrySettings


def test_defaults_export_otlp_and_capture_no_content() -> None:
    settings = TelemetrySettings.from_env({})
    assert settings == TelemetrySettings(exporter="otlp", capture_content=False)


@pytest.mark.parametrize(
    ("value", "expected"), [("otlp", "otlp"), ("console", "console"), ("none", "none"), (" OTLP ", "otlp")]
)
def test_traces_exporter_is_read_from_env(value: str, expected: str) -> None:
    assert TelemetrySettings.from_env({"OTEL_TRACES_EXPORTER": value}).exporter == expected


@pytest.mark.parametrize("value", ["zipkin", "otlp,console", ""])
def test_unknown_traces_exporter_fails_fast(value: str) -> None:
    with pytest.raises(ValueError, match="OTEL_TRACES_EXPORTER"):
        TelemetrySettings.from_env({"OTEL_TRACES_EXPORTER": value})


@pytest.mark.parametrize("var", ["OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"])
def test_only_http_protobuf_otlp_is_supported(var: str) -> None:
    assert TelemetrySettings.from_env({var: "http/protobuf"}).exporter == "otlp"
    with pytest.raises(ValueError, match=var):
        TelemetrySettings.from_env({var: "grpc"})


def test_protocol_is_ignored_when_not_exporting_otlp() -> None:
    settings = TelemetrySettings.from_env({"OTEL_TRACES_EXPORTER": "none", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"})
    assert settings.exporter == "none"


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("1", True), ("False", False), ("0", False)])
def test_content_capture_switch(value: str, expected: bool) -> None:
    assert TelemetrySettings.from_env({"ONB_TELEMETRY_CAPTURE_CONTENT": value}).capture_content is expected


def test_bad_content_capture_value_fails_fast() -> None:
    with pytest.raises(ValueError, match="ONB_TELEMETRY_CAPTURE_CONTENT"):
        TelemetrySettings.from_env({"ONB_TELEMETRY_CAPTURE_CONTENT": "maybe"})


def test_invalid_exporter_in_constructor_is_rejected() -> None:
    with pytest.raises(ValueError, match="exporter"):
        TelemetrySettings(exporter="zipkin")  # type: ignore[arg-type]  # the point: a value outside the Literal


def test_from_env_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("ONB_TELEMETRY_CAPTURE_CONTENT", "true")
    assert TelemetrySettings.from_env() == TelemetrySettings(exporter="console", capture_content=True)
