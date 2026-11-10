"""The span pipeline: hooks see every span, in order, before the export stage does.

This is where OBS-1's PII redaction processor plugs in (PRD §7.7). The SDK's own
`TracerProvider.add_span_processor` appends after the exporter, so a processor added that way may
see a span only after it has left the process; hooks registered here always run first.

An ended span is read-only in the SDK, so a hook that must change what is exported subclasses
`RedactingSpanProcessor` and returns a changed copy (`copy_span`) or None to drop the span. Each
later hook, and finally the export stage, receives what the previous hook returned.

A pipeline may also have a `final_hook`: a redacting hook that runs after every registered hook and
immediately before export, so what it guarantees (OBS-1's PII scrubbing) holds whatever a hook did.

Fail closed, never break the caller:
- a hook that raises in `on_end` drops that span (it is not exported);
- a hook that raises in `on_start` is logged, the caller carries on, and that span is dropped;
- after `shutdown()` begins, no span is exported.
Failure logs name the hook and the exception type only, never span content or the message.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

_log = logging.getLogger("onb_telemetry")

_COPYABLE = (
    "name",
    "context",
    "parent",
    "resource",
    "attributes",
    "events",
    "links",
    "kind",
    "status",
    "start_time",
    "end_time",
    "instrumentation_scope",
)


def copy_span(span: ReadableSpan, **changes: Any) -> ReadableSpan:
    """A new read-only span equal to `span` except for `changes` (any of the fields above)."""
    unknown = set(changes) - set(_COPYABLE)
    if unknown:
        raise TypeError(f"copy_span got unknown fields: {sorted(unknown)}")
    fields = {name: getattr(span, name) for name in _COPYABLE}
    return ReadableSpan(**{**fields, **changes})


class RedactingSpanProcessor(SpanProcessor):
    """A hook that may replace or drop a span before export. Override `redact`."""

    def redact(self, span: ReadableSpan) -> ReadableSpan | None:
        return span


class HookedSpanProcessor(SpanProcessor):
    """Runs `hooks` in registration order, then the export processor."""

    def __init__(
        self, export_processor: SpanProcessor | None, *, final_hook: RedactingSpanProcessor | None = None
    ) -> None:
        self._lock = threading.Lock()
        self._final_hook = final_hook
        self._hooks: tuple[SpanProcessor, ...] = ()
        self._export = export_processor
        self._closed = False
        self._poisoned: set[int] = set()

    @property
    def hooks(self) -> tuple[SpanProcessor, ...]:
        return self._hooks

    @property
    def final_hook(self) -> RedactingSpanProcessor | None:
        return self._final_hook

    @property
    def export_processor(self) -> SpanProcessor | None:
        return self._export

    def add_hook(self, processor: SpanProcessor) -> None:
        # Copy-on-write: a span ending on another thread iterates a complete, unchanging tuple.
        with self._lock:
            self._hooks = (*self._hooks, processor)

    def set_export_processor(self, processor: SpanProcessor | None) -> None:
        """Replace the export stage; the old one is flushed and shut down (tests use this)."""
        with self._lock:
            old, self._export = self._export, processor
        if old is not None:
            _guarded(old, "force_flush", lambda: old.force_flush())
            _guarded(old, "shutdown", old.shutdown)

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for hook in self._stages():
            try:
                hook.on_start(span, parent_context=parent_context)
            except Exception as exc:
                _log_failure(hook, "on_start", exc)
                with self._lock:
                    self._poisoned.add(span.context.span_id)
                return
        if self._export is not None:
            self._export.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if self._closed or self._was_poisoned(span):
            return
        current = span
        for hook in self._stages():
            try:
                result = _run_hook(hook, current)
            except Exception as exc:
                _log_failure(hook, "on_end", exc)
                return
            if result is None:
                return  # a redacting hook dropped the span
            current = result
        export = self._export
        if export is not None:
            export.on_end(current)

    def shutdown(self) -> None:
        self._closed = True  # first: nothing ending from now on reaches export, hooked or not
        if self._export is not None:
            _guarded(self._export, "shutdown", self._export.shutdown)
        for hook in self._stages():
            _guarded(hook, "shutdown", hook.shutdown)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        stages = [*self._stages(), *([self._export] if self._export is not None else [])]
        results = [_guarded(stage, "force_flush", lambda s=stage: s.force_flush(timeout_millis)) for stage in stages]
        return all(result is True for result in results)

    def _stages(self) -> tuple[SpanProcessor, ...]:
        final = self._final_hook
        return self._hooks if final is None else (*self._hooks, final)

    def _was_poisoned(self, span: ReadableSpan) -> bool:
        if not self._poisoned or span.context is None:
            return False
        with self._lock:
            if span.context.span_id in self._poisoned:
                self._poisoned.discard(span.context.span_id)
                return True
        return False


def _run_hook(hook: SpanProcessor, span: ReadableSpan) -> ReadableSpan | None:
    if isinstance(hook, RedactingSpanProcessor):
        return hook.redact(span)
    hook.on_end(span)
    return span


def _guarded(stage: SpanProcessor, step: str, call: Any) -> object:
    try:
        return call()
    except Exception as exc:
        _log_failure(stage, step, exc)
        return False


def _log_failure(stage: SpanProcessor, step: str, exc: Exception) -> None:
    # No span name, attributes or exception message: any of them may carry the PII being redacted.
    _log.error("span hook %s failed in %s with %s", type(stage).__name__, step, type(exc).__name__)
