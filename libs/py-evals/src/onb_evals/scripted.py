"""The self-test agent and its scripted model (PRD §8.2 "CI runs a harness self-test with the fake connector").

`ScriptedAgent` is a minimal tool-calling loop: it redacts free text before the model sees it (PRD §7.10),
binds the toolbox, runs tool calls the model asks for, and parses the final answer as the structured
output. `ScriptedLLM` builds its model through `LLMRuntime` with LLM-1's `fake` connector replaying a
per-case script, so every call is a real `llm.chat` span. No model is ever called.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from onb_guardrails.pii import redact
from onb_llm import LLMRuntime, ResolvedLLM, discover_connectors, load_llm_config

from onb_evals.capture import to_text
from onb_evals.runner import AgentInvocation
from onb_evals.tokens import TokenMap

__all__ = ["AgentLoopError", "ScriptedAgent", "ScriptedLLM", "fake_runtime", "find_repo_file"]

MAX_TURNS: Final = 8
SCRIPTED_MODEL: Final = "eval-scripted"
_FAKE_ENV: Final = {
    "LLM_CONNECTOR": "fake",
    "LLM_MODEL_FAST": "fake-fast",
    "LLM_MODEL_STANDARD": "fake-standard",
    "LLM_MODEL_DEEP": "fake-deep",
}
_SYSTEM: Final = "You are the {agent} agent{mode} under evaluation. Use the tools; answer with one JSON object."


class AgentLoopError(RuntimeError):
    """The agent did not produce a final structured answer."""


def find_repo_file(relative: str, start: Path | None = None) -> Path:
    """`relative` in the nearest ancestor directory (of `start`, else this file) that has it."""
    here = (start or Path(__file__)).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / relative
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{relative} not found above {here}")


def fake_runtime(env: Mapping[str, str] | None = None) -> LLMRuntime:
    """An `LLMRuntime` with every alias on the `fake` connector (no credentials, no model calls)."""
    merged = {**os.environ, **(env or {}), **_FAKE_ENV}
    config = load_llm_config(find_repo_file("config/llm.yaml"), merged)
    return LLMRuntime.start(config, discover_connectors(), merged)


class ScriptedLLM:
    """A `fake` model replaying `turns`; seed token names in the turns become the issued tokens."""

    def __init__(self, runtime: LLMRuntime, turns: Sequence[Mapping[str, Any]]) -> None:
        self._runtime = runtime
        self._turns = [dict(turn) for turn in turns]
        self.label = "fake:" + SCRIPTED_MODEL

    def chat_model(self, callbacks: Sequence[BaseCallbackHandler], tokens: TokenMap) -> BaseChatModel:
        resolved = ResolvedLLM(
            connector="fake",
            model=SCRIPTED_MODEL,
            params={"script": tokens.to_real(self._turns)},
            source="manifest",
            alias=None,
        )
        model = self._runtime.chat_model(resolved)
        existing = model.callbacks if isinstance(model.callbacks, list) else []
        return model.model_copy(update={"callbacks": [*existing, *callbacks]})


def _final(message: AIMessage) -> dict[str, Any]:
    try:
        output = json.loads(message.text)
    except json.JSONDecodeError:
        raise AgentLoopError("the final answer is not JSON") from None
    if not isinstance(output, dict):
        raise AgentLoopError("the final answer is not a JSON object")
    return output


class ScriptedAgent:
    """The agent adapter the self-test uses for every agent name."""

    async def invoke(self, invocation: AgentInvocation) -> dict[str, Any]:
        case = invocation.case
        mode = f" ({case.mode} mode)" if case.mode else ""
        model = invocation.chat_model.bind_tools(invocation.tools.tool_schemas())
        messages: list[BaseMessage] = [
            SystemMessage(_SYSTEM.format(agent=case.agent, mode=mode)),
            HumanMessage(redact(to_text(dict(invocation.input)))),
        ]
        for _ in range(MAX_TURNS):
            reply = await model.ainvoke(messages)
            messages.append(reply)
            if not reply.tool_calls:
                return _final(reply)
            for call in reply.tool_calls:
                result = await invocation.tools.call(call["name"], call["args"])
                messages.append(ToolMessage(content=to_text(result), tool_call_id=str(call["id"])))
        raise AgentLoopError(f"no final answer after {MAX_TURNS} turns")
