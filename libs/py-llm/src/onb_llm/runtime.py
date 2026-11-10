"""`LLMRuntime`: the validated configuration plus the installed connectors, built once at startup.

`LLMRuntime.start` fails fast: every connector the default and the aliases select must be installed
and have its required environment variables set. A selection that only a manifest or an org makes is
checked when it is resolved, before any model is built.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager, Callbacks
from langchain_core.language_models import BaseChatModel

from onb_llm.config import LLMConfig, LLMSelection
from onb_llm.connector import LLMConnector
from onb_llm.errors import ConnectorEnvironmentError, ConnectorNotFoundError
from onb_llm.registry import ConnectorRegistry
from onb_llm.resolution import ResolvedLLM, resolve_llm
from onb_llm.telemetry import LLMSpanHandler


class LLMRuntime:
    def __init__(self, config: LLMConfig, registry: ConnectorRegistry, env: Mapping[str, str]) -> None:
        self._config = config
        self._registry = registry
        self._env = dict(env)

    @classmethod
    def start(cls, config: LLMConfig, registry: ConnectorRegistry, env: Mapping[str, str]) -> LLMRuntime:
        """Validate every configured selection against `registry` and `env`, then return the runtime.

        `env` is passed in (usually `os.environ`) so the check sees exactly what the service will use.
        """
        runtime = cls(config, registry, env)
        for connector, selected_by in _configured_connectors(config).items():
            runtime._check(connector, selected_by)
        return runtime

    @property
    def config(self) -> LLMConfig:
        return self._config

    @property
    def registry(self) -> ConnectorRegistry:
        return self._registry

    def connector(self, name: str) -> LLMConnector:
        return self._registry.get(name)

    def resolve(self, *, manifest: LLMSelection | None = None, org_override: LLMSelection | None = None) -> ResolvedLLM:
        """Resolve manifest → org override → default, and check the chosen connector can run."""
        resolved = resolve_llm(self._config, manifest=manifest, org_override=org_override)
        self._check(resolved.connector, (resolved.source,))
        return resolved

    def chat_model(self, resolved: ResolvedLLM) -> BaseChatModel:
        """A new chat model for `resolved`, traced with an `llm.chat` span per call."""
        connector = self._check(resolved.connector, (resolved.source,))
        created = connector.create_chat_model(resolved.model, resolved.params)
        handler = LLMSpanHandler(resolved.connector, resolved.model)
        return created.model_copy(update={"callbacks": _with_handler(created.callbacks, handler)})

    def chat_model_for(
        self, *, manifest: LLMSelection | None = None, org_override: LLMSelection | None = None
    ) -> tuple[ResolvedLLM, BaseChatModel]:
        """Resolve and build in one step; keep the `ResolvedLLM` to record on the task."""
        resolved = self.resolve(manifest=manifest, org_override=org_override)
        return resolved, self.chat_model(resolved)

    def _check(self, name: str, selected_by: tuple[str, ...]) -> LLMConnector:
        try:
            connector = self._registry.get(name)
        except ConnectorNotFoundError as error:
            raise ConnectorNotFoundError(f"{error} (selected by {', '.join(selected_by)})") from error
        missing = tuple(var for var in connector.required_env if not self._env.get(var, "").strip())
        if missing:
            raise ConnectorEnvironmentError(name, missing, selected_by)
        return connector


def _with_handler(callbacks: Callbacks, handler: BaseCallbackHandler) -> Callbacks:
    """The connector's own callbacks (a list or a manager) plus `handler`, without changing the original."""
    if isinstance(callbacks, BaseCallbackManager):
        manager = callbacks.copy()
        manager.add_handler(handler, inherit=True)
        return manager
    return [*(callbacks or []), handler]


def _configured_connectors(config: LLMConfig) -> dict[str, tuple[str, ...]]:
    """Connector name → the config keys that select it."""
    selected: defaultdict[str, list[str]] = defaultdict(list)
    if config.default.connector is not None:
        selected[config.default.connector].append("default")
    for alias, selection in config.aliases.items():
        if selection.connector is not None:
            label = f"aliases.{alias}"
            if config.default.alias == alias:
                label = f"default via {label}"
            selected[selection.connector].append(label)
    return {name: tuple(keys) for name, keys in selected.items()}
