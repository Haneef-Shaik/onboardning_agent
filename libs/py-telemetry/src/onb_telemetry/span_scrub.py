"""The PII redaction span processor (PRD §7.7, §7.10 "Logs & traces").

Every span is scrubbed with the PII-3 detectors (`onb_guardrails.pii.redact`) before it is exported:
raw values become typed markers (`[MOBILE]`, `[PAN]`, `[EMAIL]`, …) and `ptk_` tokens pass through,
because the detectors never match a token.

What is scrubbed: the span name, attribute keys and values (strings, numbers and sequences of them),
event names and attributes (so `exception.message` and `exception.stacktrace` too), link attributes
and the status description. Resource attributes are the service's own identity and are left alone.

`init_telemetry` installs this processor as the pipeline's final hook, after every registered hook,
so no hook can put a raw value back. If scrubbing raises, the pipeline drops the span: a span is
never exported unscrubbed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from onb_guardrails.pii import redact
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import AttributeValue

from onb_telemetry.pipeline import RedactingSpanProcessor, copy_span


class PiiScrubbingSpanProcessor(RedactingSpanProcessor):
    """Returns a copy of the span with every detected PII value replaced by its marker."""

    def redact(self, span: ReadableSpan) -> ReadableSpan | None:
        return copy_span(
            span,
            name=redact(span.name),
            attributes=scrub_attributes(span.attributes),
            events=tuple(_scrub_event(event) for event in span.events),
            links=tuple(_scrub_link(link) for link in span.links),
            status=_scrub_status(span.status),
        )


def scrub_attributes(attributes: Mapping[str, AttributeValue] | None) -> dict[str, AttributeValue]:
    """A new attribute dict with PII removed from keys and values."""
    return {redact(key): scrub_value(value) for key, value in (attributes or {}).items()}


def scrub_value(value: AttributeValue) -> AttributeValue:
    """Strings are redacted. A number is kept unless its digits are PII, when it becomes the marker
    string. A sequence is scrubbed element-wise and, if any element had to become a string, every
    element does, because OTel attribute sequences must hold one type."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        text = str(value)
        scrubbed = redact(text)
        return value if scrubbed == text else scrubbed
    if isinstance(value, Sequence):
        items = tuple(scrub_value(item) for item in value)
        if any(type(new) is not type(old) for new, old in zip(items, value, strict=True)):
            return tuple(str(item) for item in items)
        return items
    return value


def _scrub_event(event: Event) -> Event:
    return Event(redact(event.name), scrub_attributes(event.attributes), timestamp=event.timestamp)


def _scrub_link(link: Link) -> Link:
    return Link(link.context, scrub_attributes(link.attributes))


def _scrub_status(status: Status) -> Status:
    if status.description is None:
        return status
    return Status(status.status_code, redact(status.description))
