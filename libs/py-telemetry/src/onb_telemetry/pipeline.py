"""The span pipeline: registered hooks see every span, in order, before the export stage does.

This is where OBS-1's PII redaction processor plugs in (PRD §7.7). The SDK's own
`TracerProvider.add_span_processor` appends after the exporter, so a processor added that way may
see a span only after it has left the process; hooks registered here always run first.

Fail closed: if a hook raises in `on_end`, that span is not exported. A redaction hook that breaks
must not let the unredacted span out.
"""

from __future__ import annotations

import logging
import threading

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

_log = logging.getLogger("onb_telemetry")


class HookedSpanProcessor(SpanProcessor):
    """Runs `hooks` in registration order, then the export processor."""

    def __init__(self, export_processor: SpanProcessor | None) -> None:
        self._lock = threading.Lock()
        self._hooks: tuple[SpanProcessor, ...] = ()
        self._export = export_processor

    @property
    def hooks(self) -> tuple[SpanProcessor, ...]:
        return self._hooks

    @property
    def export_processor(self) -> SpanProcessor | None:
        return self._export

    def add_hook(self, processor: SpanProcessor) -> None:
        # Copy-on-write: a span ending on another thread iterates a complete, unchanging tuple.
        with self._lock:
            self._hooks = (*self._hooks, processor)

    def set_export_processor(self, processor: SpanProcessor | None) -> None:
        """Replace the export stage (tests route export into a journal or an in-memory exporter)."""
        with self._lock:
            self._export = processor

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for hook in self._hooks:
            hook.on_start(span, parent_context=parent_context)
        if self._export is not None:
            self._export.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        for hook in self._hooks:
            try:
                hook.on_end(span)
            except Exception:
                _log.exception("span hook %s failed; span %r dropped, not exported", type(hook).__name__, span.name)
                return
        if self._export is not None:
            self._export.on_end(span)

    def shutdown(self) -> None:
        for hook in self._hooks:
            hook.shutdown()
        if self._export is not None:
            self._export.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        results = [hook.force_flush(timeout_millis) for hook in self._hooks]
        if self._export is not None:
            results.append(self._export.force_flush(timeout_millis))
        return all(results)
