"""The `fake` connector: a deterministic scripted chat model for unit tests, eval self-tests and CI.

Each call returns the next scripted turn; the script running out is an error, not a silent repeat.
Tool calls in the script must name a bound tool. Every call's input is kept (`calls()`), so tests can
assert on exactly what the model was sent.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from onb_llm.connector import HealthStatus
from onb_llm.connectors.base import ping
from onb_llm.connectors.script import DEFAULT_SCRIPT, ScriptedTurn, script_from_params
from onb_llm.errors import LLMConfigError, LLMError


class ScriptExhaustedError(LLMError):
    """The model was called more times than its script has turns."""


@dataclass(frozen=True)
class ScriptedCall:
    """What one call sent the model: the messages and the names of the bound tools."""

    messages: tuple[BaseMessage, ...]
    tool_names: tuple[str, ...]


class ScriptedChatModel(BaseChatModel):
    model_name: str
    script: tuple[ScriptedTurn, ...]

    _cursor: int = PrivateAttr(default=0)
    _calls: list[ScriptedCall] = PrivateAttr(default_factory=list)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "onb-fake-scripted"

    def __copy__(self) -> ScriptedChatModel:
        """A copy replays the script from the start with its own call log and lock (pydantic's
        `model_copy` would otherwise share the log and lock with the original)."""
        return type(self)(model_name=self.model_name, script=self.script, callbacks=self.callbacks)

    def calls(self) -> tuple[ScriptedCall, ...]:
        with self._lock:
            return tuple(self._calls)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_names = tuple(tool["function"]["name"] for tool in kwargs.get("tools", ()))
        with self._lock:
            turn = self._cursor
            self._calls.append(ScriptedCall(messages=tuple(messages), tool_names=tool_names))
            if turn >= len(self.script):
                raise ScriptExhaustedError(
                    f"fake model '{self.model_name}' was called {turn + 1} times "
                    f"but has {len(self.script)} scripted turns"
                )
            self._cursor = turn + 1
        message = self.script[turn].to_message(turn)
        _check_tools(message, tool_names)
        if message.usage_metadata is None:
            message = message.model_copy(update={"usage_metadata": _estimate_usage(messages, message)})
        return ChatResult(generations=[ChatGeneration(message=message)])


def _check_tools(message: AIMessage, bound: tuple[str, ...]) -> None:
    for call in message.tool_calls:
        if call["name"] not in bound:
            raise LLMConfigError(
                f"scripted tool call '{call['name']}' names no bound tool; bound: {', '.join(bound) or 'none'}"
            )


def _words(message: BaseMessage) -> int:
    return len(message.text.split())


def _estimate_usage(messages: list[BaseMessage], reply: AIMessage) -> UsageMetadata:
    """A deterministic stand-in for token counts: one token per whitespace-separated word."""
    input_tokens = sum(_words(message) for message in messages)
    output_tokens = _words(reply)
    return UsageMetadata(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens
    )


class FakeConnector:
    name = "fake"
    required_env: tuple[str, ...] = ()

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        script = script_from_params(params)
        return ScriptedChatModel(model_name=model, script=DEFAULT_SCRIPT if script is None else script)

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        return ping(self.create_chat_model(model, {}))
