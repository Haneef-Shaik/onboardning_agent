"""The PII redaction span processor (PRD §7.7, §7.10 "Logs & traces").

Every span is scrubbed with the PII-3 detectors (`onb_guardrails.pii.redact`) before it is exported:
raw values become typed markers (`[MOBILE]`, `[PAN]`, `[EMAIL]`, …) and `ptk_` tokens pass through,
because the detectors never match a token. Values are scrubbed by `onb_telemetry.scrub_values`.

What is scrubbed: the span name, attribute keys and values (strings, numbers and sequences of them),
event names and attributes (so `exception.message` and `exception.stacktrace` too), link
attributes, the status description, and the instrumentation scope's name and attributes. The
resource is scrubbed once, when `init_telemetry` builds it. Dropped-attribute counts are kept.

`init_telemetry` installs this processor as the pipeline's final hook, after every registered hook,
so no hook can put a raw value back. If scrubbing raises, the pipeline drops the span: a span is
never exported unscrubbed. It runs on the thread that ends the span.

Not scrubbed: a link's `tracestate` (vendor key/values from an incoming header; see the handoff).
"""

from __future__ import annotations

from collections.abc import Mapping

from onb_guardrails.pii import redact
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import AttributeValue

from onb_telemetry.pipeline import RedactingSpanProcessor, copy_span
from onb_telemetry.scrub_values import scrub_attributes


class PiiScrubbingSpanProcessor(RedactingSpanProcessor):
    """Returns a copy of the span with every detected PII value replaced by its marker."""

    def redact(self, span: ReadableSpan) -> ReadableSpan | None:
        return copy_span(
            span,
            name=redact(span.name),
            attributes=scrubbed_bounded(span.attributes, span.dropped_attributes),
            events=tuple(_scrub_event(event) for event in span.events),
            links=tuple(_scrub_link(link) for link in span.links),
            status=_scrub_status(span.status),
            instrumentation_scope=_scrub_scope(span.instrumentation_scope),
        )


def scrubbed_bounded(attributes: Mapping[str, AttributeValue] | None, dropped: int | None = None) -> BoundedAttributes:
    """Scrubbed, immutable attributes that keep the original's dropped count."""
    bounded = BoundedAttributes(maxlen=None, attributes=scrub_attributes(attributes), immutable=True)
    bounded.dropped = getattr(attributes, "dropped", 0) if dropped is None else dropped
    return bounded


def _scrub_event(event: Event) -> Event:
    return Event(redact(event.name), scrubbed_bounded(event.attributes), timestamp=event.timestamp)


def _scrub_link(link: Link) -> Link:
    return Link(link.context, scrubbed_bounded(link.attributes))


def _scrub_status(status: Status) -> Status:
    if status.description is None:
        return status
    return Status(status.status_code, redact(status.description))


def _scrub_scope(scope: InstrumentationScope | None) -> InstrumentationScope | None:
    if scope is None:
        return None
    return InstrumentationScope(
        redact(scope.name), scope.version, scope.schema_url, scrub_attributes(scope.attributes) or None
    )
