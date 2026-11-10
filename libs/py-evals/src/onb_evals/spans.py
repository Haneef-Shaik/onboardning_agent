"""Eval spans: every span started while a case runs carries `eval.run_id` and `eval.case_id` (PRD §8.2).

`EvalSpans` is a pipeline hook (`init_telemetry(processors=[...])` or `register_span_processor`): on start
it stamps the current case's ids on the span; on end it keeps the span's text for the `trace` scope of
`no_raw_pii`. It sees spans before OBS-1's scrubber, so the trace check is stricter than what is exported.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.util.types import AttributeValue

__all__ = ["EVAL_CASE_ID", "EVAL_REPETITION", "EVAL_RUN_ID", "EvalSpans", "case_span"]

EVAL_RUN_ID: Final = "eval.run_id"
EVAL_CASE_ID: Final = "eval.case_id"
EVAL_REPETITION: Final = "eval.repetition"

CaseKey = tuple[str, str, int]
_current: ContextVar[CaseKey | None] = ContextVar("onb_eval_case", default=None)


def _key_of(span: ReadableSpan) -> CaseKey | None:
    attributes = span.attributes or {}
    run, case, rep = attributes.get(EVAL_RUN_ID), attributes.get(EVAL_CASE_ID), attributes.get(EVAL_REPETITION)
    if isinstance(run, str) and isinstance(case, str) and isinstance(rep, int):
        return run, case, rep
    return None


def span_text(span: ReadableSpan) -> str:
    events = [{"name": e.name, **dict(e.attributes or {})} for e in span.events]
    body = {"attributes": dict(span.attributes or {}), "events": events, "status": span.status.description}
    return f"{span.name} {json.dumps(body, default=str, sort_keys=True)}"


class EvalSpans(SpanProcessor):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._texts: dict[CaseKey, list[str]] = {}

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        key = _current.get()
        if key is not None:
            span.set_attributes({EVAL_RUN_ID: key[0], EVAL_CASE_ID: key[1], EVAL_REPETITION: key[2]})

    def on_end(self, span: ReadableSpan) -> None:
        key = _key_of(span)
        if key is not None:
            with self._lock:
                self._texts.setdefault(key, []).append(span_text(span))

    def collect(self, key: CaseKey) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._texts.pop(key, []))

    def shutdown(self) -> None:
        with self._lock:
            self._texts.clear()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


@contextmanager
def case_span(key: CaseKey, *, agent: str, org_id: str) -> Iterator[None]:
    """The `eval.case` root span; spans started inside it are stamped by `EvalSpans`."""
    token = _current.set(key)
    try:
        attributes: dict[str, AttributeValue] = {
            EVAL_RUN_ID: key[0],
            EVAL_CASE_ID: key[1],
            EVAL_REPETITION: key[2],
            "agent.name": agent,
            "org_id": org_id,
        }
        with trace.get_tracer("onb_evals").start_as_current_span("eval.case", attributes=attributes):
            yield
    finally:
        _current.reset(token)
