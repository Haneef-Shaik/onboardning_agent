"""OpenTelemetry setup for every Python service (PRD §5.7, §7.7).

Call `init_telemetry("orchestrator")` once at startup. When work is handed off, store
`inject_traceparent()` on the record; when it is picked up again, run it inside
`continue_from(record["traceparent"], "run.resume")` so it joins the run's trace.
"""

from onb_telemetry.attributes import set_span_attributes, span_attributes
from onb_telemetry.propagation import continue_from, inject_traceparent
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.setup import (
    TelemetryHandle,
    active_telemetry,
    init_telemetry,
    instrument_app,
    register_span_processor,
)

__all__ = [
    "PROJECT",
    "TelemetryHandle",
    "TelemetrySettings",
    "active_telemetry",
    "continue_from",
    "init_telemetry",
    "inject_traceparent",
    "instrument_app",
    "register_span_processor",
    "set_span_attributes",
    "span_attributes",
]

PROJECT = "py-telemetry"
