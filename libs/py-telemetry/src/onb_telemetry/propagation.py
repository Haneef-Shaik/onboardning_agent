"""Keep a run in one trace across async waits (PRD §5.7, §7.7).

Store `inject_traceparent()` on the event, job or run record when the work is handed off; when it
is picked up again, open the continuation with `continue_from(record.traceparent, name)`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

TRACEPARENT = "traceparent"

_log = logging.getLogger("onb_telemetry")
# Explicit W3C propagator: what is stored must not depend on the process's global propagator config.
_PROPAGATOR = TraceContextTextMapPropagator()
_TRACER_NAME = "onb_telemetry"


def inject_traceparent() -> str | None:
    """The W3C `traceparent` of the current span, or None when no span is recording a trace."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get(TRACEPARENT)


@contextmanager
def continue_from(
    traceparent: str | None,
    name: str,
    *,
    attributes: Mapping[str, AttributeValue] | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Iterator[trace.Span]:
    """Start `name` as a child of the span a stored `traceparent` names, and make it current.

    A missing or unparseable value starts a new root trace instead (logged), so a damaged record
    never stops the work it describes.
    """
    parent = _PROPAGATOR.extract({TRACEPARENT: traceparent}) if traceparent else None
    if traceparent and not trace.get_current_span(parent).get_span_context().is_valid:
        _log.warning("stored traceparent is not valid W3C trace context; starting a new trace")
        parent = None
    tracer = trace.get_tracer(_TRACER_NAME)
    # An empty Context (not None) as parent: never adopt whatever happens to be current here.
    with tracer.start_as_current_span(name, context=parent or Context(), kind=kind, attributes=attributes) as span:
        yield span
