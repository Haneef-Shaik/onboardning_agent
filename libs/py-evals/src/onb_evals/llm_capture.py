"""Captures what the agent sent the model: the text of every LLM input, steps and token usage.

A LangChain callback handler attached to the chat model the runner hands the agent. One step is one
chat-model call (one turn of the agent loop).
"""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import LLMResult

__all__ = ["LLMCapture", "render_messages"]


def _render(message: BaseMessage) -> str:
    text = f"{message.type}: {message.text}"
    if isinstance(message, AIMessage) and message.tool_calls:
        text += " " + json.dumps([{"name": c["name"], "args": c["args"]} for c in message.tool_calls], default=str)
    return text


def render_messages(messages: list[BaseMessage]) -> str:
    return "\n".join(_render(m) for m in messages)


class LLMCapture(BaseCallbackHandler):
    run_inline = True  # called on the agent's own task, so counts are complete when the call returns

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inputs: list[str] = []
        self.steps = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        with self._lock:
            self.steps += 1
            self.inputs.extend(render_messages(batch) for batch in messages)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if usage:
                    with self._lock:
                        self.input_tokens += int(usage.get("input_tokens", 0))
                        self.output_tokens += int(usage.get("output_tokens", 0))
