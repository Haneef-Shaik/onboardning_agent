"""The `LLMConnector` protocol (PRD §7.1). Agents only ever see the `BaseChatModel` it returns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from langchain_core.language_models import BaseChatModel


@dataclass(frozen=True)
class HealthStatus:
    """Outcome of one healthcheck call. `detail` is the reply, or the exception type on failure."""

    ok: bool
    detail: str
    latency_ms: float | None = None


@runtime_checkable
class LLMConnector(Protocol):
    """A provider plugin, registered under the `onboarding.llm_connectors` entry-point group.

    The entry point names a zero-argument callable (usually the class) that returns the connector;
    the entry point's name must equal the connector's `name`.
    """

    name: str
    required_env: tuple[str, ...]
    """Environment variables that must be set before the connector is used; checked at startup."""

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        """A new chat model for `model`. Raise `LLMConfigError` for bad params."""
        ...

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        """Make one tiny real call to `model` and report whether it answered. Never raises."""
        ...
