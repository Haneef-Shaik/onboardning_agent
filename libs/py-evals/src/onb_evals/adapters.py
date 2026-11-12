"""Agent adapters for full eval runs, and the model the run selects (`--llm <alias|connector:model>`).

An agent's own goal (TB-1, AGT-1, HIL-1, CHAT-1) registers how the harness invokes it:
`register_adapter("db_agent", DbAgentAdapter)`. Until then `run --agent db_agent` stops with a clear
message. The self-test does not use this registry: it runs `ScriptedAgent` for every agent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from onb_llm import LLMRuntime, LLMSelection

from onb_evals.runner import AgentAdapter
from onb_evals.tokens import TokenMap

__all__ = ["AdapterNotFoundError", "RuntimeLLM", "adapter_for", "is_registered", "parse_llm", "register_adapter"]

_ADAPTERS: Final[dict[str, Callable[[], AgentAdapter]]] = {}


class AdapterNotFoundError(LookupError):
    pass


def register_adapter(agent: str, factory: Callable[[], AgentAdapter]) -> None:
    if agent in _ADAPTERS:
        raise ValueError(f"an adapter for {agent!r} is already registered")
    _ADAPTERS[agent] = factory


def is_registered(agent: str) -> bool:
    return agent in _ADAPTERS


def adapter_for(agent: str) -> AgentAdapter:
    factory = _ADAPTERS.get(agent)
    if factory is None:
        # The agents' own goals register their adapters here (CHAT-1: `run_assistant`); a missing one is below.
        from onb_evals.agents import register_builtin_adapters

        if agent not in _ADAPTERS:
            register_builtin_adapters()
        factory = _ADAPTERS.get(agent)
    if factory is None:
        raise AdapterNotFoundError(
            f"no eval adapter is registered for agent {agent!r}; the agent's goal registers one with "
            "onb_evals.adapters.register_adapter (the self-test runs every agent: `make evals-selftest`)"
        )
    return factory()


def parse_llm(label: str) -> LLMSelection:
    """`standard` → that alias; `bedrock:<model id>` → that connector and model."""
    connector, sep, model = label.partition(":")
    if sep:
        return LLMSelection(connector=connector, model=model)
    return LLMSelection(alias=label)


class RuntimeLLM:
    """The configured model for a full run, built through LLM-1's runtime (spans and fail-fast env included)."""

    def __init__(self, runtime: LLMRuntime, label: str) -> None:
        self._runtime = runtime
        self._resolved = runtime.resolve(manifest=parse_llm(label))
        self.label = f"{self._resolved.connector}:{self._resolved.model}"

    def chat_model(self, callbacks: Sequence[BaseCallbackHandler], tokens: TokenMap) -> BaseChatModel:
        model = self._runtime.chat_model(self._resolved)
        existing = model.callbacks if isinstance(model.callbacks, list) else []
        return model.model_copy(update={"callbacks": [*existing, *callbacks]})
