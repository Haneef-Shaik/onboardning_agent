"""One `llm.chat` span per chat-model call, with `llm.connector`, `llm.model` and token usage (PRD §7.7).

The span holds no prompt or completion text, and a failure is recorded by exception type only, so
nothing personal reaches the trace from here.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

SPAN_NAME = "llm.chat"
LLM_CONNECTOR = "llm.connector"
LLM_MODEL = "llm.model"
INPUT_TOKENS = "llm.usage.input_tokens"
OUTPUT_TOKENS = "llm.usage.output_tokens"
TOTAL_TOKENS = "llm.usage.total_tokens"
ERROR_TYPE = "error.type"


class LLMSpanHandler(BaseCallbackHandler):
    """LangChain callback that opens a span when a chat model starts and closes it when it ends."""

    raise_error = False  # a tracing fault must never break the LLM call

    def __init__(self, connector: str, model: str) -> None:
        self._attributes = {LLM_CONNECTOR: connector, LLM_MODEL: model}
        self._spans: dict[UUID, Span] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        span = trace.get_tracer("onb_llm").start_span(SPAN_NAME, kind=SpanKind.CLIENT, attributes=self._attributes)
        with self._lock:
            self._spans[run_id] = span

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        span = self._pop(run_id)
        if span is None:
            return
        span.set_attributes(_usage(response))
        span.end()

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        span = self._pop(run_id)
        if span is None:
            return
        kind = type(error).__name__
        span.set_attribute(ERROR_TYPE, kind)
        span.set_status(Status(StatusCode.ERROR, kind))
        span.end()

    def _pop(self, run_id: UUID) -> Span | None:
        with self._lock:
            return self._spans.pop(run_id, None)


def _usage(response: LLMResult) -> dict[str, int]:
    totals = {INPUT_TOKENS: 0, OUTPUT_TOKENS: 0, TOTAL_TOKENS: 0}
    seen = False
    for generations in response.generations:
        for generation in generations:
            if not isinstance(generation, ChatGeneration) or not isinstance(generation.message, AIMessage):
                continue
            usage = generation.message.usage_metadata
            if usage is None:
                continue
            seen = True
            totals = {
                INPUT_TOKENS: totals[INPUT_TOKENS] + usage["input_tokens"],
                OUTPUT_TOKENS: totals[OUTPUT_TOKENS] + usage["output_tokens"],
                TOTAL_TOKENS: totals[TOTAL_TOKENS] + usage["total_tokens"],
            }
    return totals if seen else {}
