"""The `bedrock` connector: `ChatBedrockConverse` (langchain-aws), the default provider (PRD §7.1).

Credentials come from the standard AWS chain (env keys, profile or role); only `AWS_REGION` is
required up front. A Bedrock Guardrail is applied when `guardrail_id` is configured (PRD §8.1: it is
additive to the platform's own guardrails, never a replacement).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel

from onb_llm.connector import HealthStatus
from onb_llm.connectors.base import build_and_ping, ping
from onb_llm.errors import LLMConfigError

_GUARDRAIL_PARAMS = ("guardrail_id", "guardrail_version", "guardrail_trace")


class BedrockConnector:
    name = "bedrock"
    required_env: tuple[str, ...] = ("AWS_REGION",)

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        if not model.strip():
            raise LLMConfigError("bedrock connector: the model id is empty")
        guardrail = _guardrail_config(params)
        rest = {key: value for key, value in params.items() if key not in (*_GUARDRAIL_PARAMS, "region_name")}
        # llm.yaml passes the region the startup check saw; the process env is only a fallback.
        region = params.get("region_name") or os.environ.get("AWS_REGION")
        try:
            return ChatBedrockConverse(model=model, region_name=region, guardrails=guardrail, **rest)
        except Exception as error:
            # Type and param names only: provider messages can carry endpoints, account ids and inputs.
            raise LLMConfigError(
                f"bedrock connector: cannot build the model ({type(error).__name__}); params given: {sorted(params)}"
            ) from None

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        return build_and_ping(lambda: self.create_chat_model(model, params), ping)


def _guardrail_config(params: Mapping[str, Any]) -> dict[str, str] | None:
    """`guardrailConfig` for Converse, or `None` when no guardrail id is set (an empty id means none)."""
    guardrail_id = str(params.get("guardrail_id") or "").strip()
    if not guardrail_id:
        return None
    version = str(params.get("guardrail_version") or "").strip()
    if not version:
        raise LLMConfigError("bedrock connector: guardrail_id is set but guardrail_version is not")
    config = {"guardrailIdentifier": guardrail_id, "guardrailVersion": version}
    trace = str(params.get("guardrail_trace") or "").strip()
    return {**config, "trace": trace} if trace else config
