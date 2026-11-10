"""The `langchain` connector: any provider LangChain's `init_chat_model` supports, by configuration alone.

The model is `"<provider>:<model>"` (or give `model_provider` in params). The provider's own package
and credentials must be installed and set; this connector requires no variables of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from onb_llm.connector import HealthStatus
from onb_llm.connectors.base import build_and_ping, ping
from onb_llm.errors import LLMConfigError


class LangChainConnector:
    name = "langchain"
    required_env: tuple[str, ...] = ()

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        if ":" not in model and not params.get("model_provider"):
            raise LLMConfigError(
                "langchain connector: name the provider, as model '<provider>:<model>' or param model_provider"
            )
        try:
            created = init_chat_model(model, **params)
        except (ValueError, ImportError) as error:
            raise LLMConfigError(f"langchain connector: cannot build model {model!r}: {error}") from error
        if not isinstance(created, BaseChatModel):  # only a configurable model is not one; we never ask for that
            raise LLMConfigError(f"langchain connector: {model!r} did not resolve to a single chat model")
        return created

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        return build_and_ping(lambda: self.create_chat_model(model, params), ping)
