"""Success answers: `respond(data)` wraps data in the envelope with this request's `meta`."""

from __future__ import annotations

from onb_contracts import Envelope, Meta, PageMeta, ok
from opentelemetry import trace


def current_trace_id() -> str | None:
    """The current trace id as 32 hex digits, so the UI can link to the trace; `None` when untraced."""
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def current_meta(page: PageMeta | None = None) -> Meta:
    return Meta(trace_id=current_trace_id(), page=page)


def respond[DataT](data: DataT, *, page: PageMeta | None = None) -> Envelope[DataT]:
    return ok(data, meta=current_meta(page))
