"""Env-driven telemetry settings (PRD §7.7).

Standard OpenTelemetry variables drive export. The OTLP endpoint, headers, timeout, sampler and
batch sizes are read by the SDK itself (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`,
`OTEL_BSP_*`, `OTEL_SDK_DISABLED`, ...); this module only validates the choices it makes itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, get_args

Exporter = Literal["otlp", "console", "none"]

TRACES_EXPORTER_VAR = "OTEL_TRACES_EXPORTER"
PROTOCOL_VARS = ("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL")
SUPPORTED_PROTOCOL = "http/protobuf"
CAPTURE_CONTENT_VAR = "ONB_TELEMETRY_CAPTURE_CONTENT"

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


@dataclass(frozen=True)
class TelemetrySettings:
    """`exporter`: where finished spans go. `capture_content`: whether LLM prompt and completion
    text may be recorded on spans; off unless explicitly switched on (PRD §7.7)."""

    exporter: Exporter = "otlp"
    capture_content: bool = False

    def __post_init__(self) -> None:
        if self.exporter not in get_args(Exporter):
            raise ValueError(f"exporter must be one of {get_args(Exporter)}, got {self.exporter!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TelemetrySettings:
        source = os.environ if env is None else env
        exporter = _exporter(source)
        if exporter == "otlp":
            _check_protocol(source)
        return cls(exporter=exporter, capture_content=_flag(source, CAPTURE_CONTENT_VAR, default=False))


def _exporter(env: Mapping[str, str]) -> Exporter:
    raw = env.get(TRACES_EXPORTER_VAR)
    if raw is None:
        return "otlp"
    value = raw.strip().lower()
    if value not in get_args(Exporter):
        raise ValueError(f"{TRACES_EXPORTER_VAR}={raw!r} is not supported; use one of {get_args(Exporter)}")
    return cast(Exporter, value)


def _check_protocol(env: Mapping[str, str]) -> None:
    for var in PROTOCOL_VARS:
        raw = env.get(var)
        if raw is not None and raw.strip().lower() != SUPPORTED_PROTOCOL:
            raise ValueError(f"{var}={raw!r} is not supported; only {SUPPORTED_PROTOCOL!r} OTLP export is installed")


def _flag(env: Mapping[str, str], var: str, *, default: bool) -> bool:
    raw = env.get(var)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{var}={raw!r} is not a boolean; use true or false")
