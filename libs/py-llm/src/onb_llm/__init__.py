"""LLM connector registry (PRD §5.6, §7.1).

At startup:

    runtime = LLMRuntime.start(load_llm_config(Path("config/llm.yaml"), os.environ), discover_connectors(), os.environ)

Per task:

    resolved, chat_model = runtime.chat_model_for(manifest=manifest.llm, org_override=org_choice)

Record `resolved` on the task; agents only ever see `chat_model`, a LangChain `BaseChatModel`.
"""

from onb_llm.config import ConnectorSettings, LLMConfig, LLMSelection, load_llm_config
from onb_llm.connector import HealthStatus, LLMConnector
from onb_llm.errors import (
    ConnectorEnvironmentError,
    ConnectorNotFoundError,
    ConnectorRegistryError,
    LLMConfigError,
    LLMError,
)
from onb_llm.interpolation import interpolate
from onb_llm.registry import ENTRY_POINT_GROUP, ConnectorRegistry, discover_connectors
from onb_llm.resolution import ResolvedLLM, resolve_llm
from onb_llm.runtime import LLMRuntime

__all__ = [
    "ENTRY_POINT_GROUP",
    "PROJECT",
    "ConnectorEnvironmentError",
    "ConnectorNotFoundError",
    "ConnectorRegistry",
    "ConnectorRegistryError",
    "ConnectorSettings",
    "HealthStatus",
    "LLMConfig",
    "LLMConfigError",
    "LLMConnector",
    "LLMError",
    "LLMRuntime",
    "LLMSelection",
    "ResolvedLLM",
    "discover_connectors",
    "interpolate",
    "load_llm_config",
    "resolve_llm",
]

PROJECT = "py-llm"
