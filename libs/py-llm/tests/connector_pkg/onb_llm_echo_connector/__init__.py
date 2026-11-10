"""A test-only LLM connector package, outside `onb_llm`.

It is "installed" by putting `tests/connector_pkg` on `sys.path`: the `.dist-info` folder next to it
registers `echo` in the `onboarding.llm_connectors` entry-point group, as `pip install` would.
"""

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from onb_llm import HealthStatus


class EchoConnector:
    name = "echo"
    required_env: tuple[str, ...] = ()

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        return FakeListChatModel(responses=[f"echo from {model}"])

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        return HealthStatus(ok=True, detail="echo is always healthy")
