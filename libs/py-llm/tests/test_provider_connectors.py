"""The `bedrock` and `langchain` connectors build real LangChain models without calling them.

Constructing `ChatBedrockConverse` makes a boto3 client but sends nothing; no credentials are needed.
The model ids here are placeholders, not real Bedrock ids.
"""

from typing import Any

import pytest
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from onb_llm import HealthStatus, LLMConfigError
from onb_llm.connectors.base import ping
from onb_llm.connectors.bedrock import BedrockConnector
from onb_llm.connectors.langchain import LangChainConnector


@pytest.fixture(autouse=True)
def _no_ambient_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")


def test_bedrock_needs_aws_region() -> None:
    assert BedrockConnector.required_env == ("AWS_REGION",)


def test_bedrock_builds_a_converse_model_from_the_given_model_id() -> None:
    chat_model = BedrockConnector().create_chat_model("placeholder-model-id", {"temperature": 0.2})
    assert isinstance(chat_model, ChatBedrockConverse)
    assert chat_model.model_id == "placeholder-model-id"
    assert chat_model.region_name == "ap-south-1"
    assert chat_model.temperature == 0.2
    assert chat_model.guardrail_config is None


def test_bedrock_applies_a_guardrail_when_one_is_configured() -> None:
    params = {"guardrail_id": "gr-test", "guardrail_version": "2", "guardrail_trace": "enabled"}
    chat_model = BedrockConnector().create_chat_model("placeholder-model-id", params)
    assert isinstance(chat_model, ChatBedrockConverse)
    assert chat_model.guardrail_config == {
        "guardrailIdentifier": "gr-test",
        "guardrailVersion": "2",
        "trace": "enabled",
    }


def test_bedrock_ignores_an_empty_guardrail_id() -> None:
    chat_model = BedrockConnector().create_chat_model(
        "placeholder-model-id", {"guardrail_id": "", "guardrail_version": ""}
    )
    assert isinstance(chat_model, ChatBedrockConverse)
    assert chat_model.guardrail_config is None


def test_a_guardrail_id_without_a_version_is_rejected() -> None:
    with pytest.raises(LLMConfigError, match="guardrail_version"):
        BedrockConnector().create_chat_model("placeholder-model-id", {"guardrail_id": "gr-test"})


def test_bedrock_uses_the_region_from_params_over_the_process_env() -> None:
    chat_model = BedrockConnector().create_chat_model("placeholder-model-id", {"region_name": "eu-west-1"})
    assert isinstance(chat_model, ChatBedrockConverse)
    assert chat_model.region_name == "eu-west-1"


def test_bedrock_reports_a_build_failure_by_type_and_param_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(**kwargs: Any) -> None:
        raise ValueError("endpoint https://internal.example for account 123456789012")

    monkeypatch.setattr("onb_llm.connectors.bedrock.ChatBedrockConverse", explode)
    with pytest.raises(LLMConfigError) as caught:
        BedrockConnector().create_chat_model("placeholder-model-id", {"temperature": 0})
    message = str(caught.value)
    assert "ValueError" in message
    assert "temperature" in message
    assert "123456789012" not in message
    assert "internal.example" not in message


def test_langchain_reports_a_build_failure_by_type_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise ValueError("api_key sk-should-not-appear rejected")

    monkeypatch.setattr("onb_llm.connectors.langchain.init_chat_model", explode)
    with pytest.raises(LLMConfigError) as caught:
        LangChainConnector().create_chat_model("someprovider:m", {})
    assert "ValueError" in str(caught.value)
    assert "sk-should-not-appear" not in str(caught.value)


def test_bedrock_rejects_an_empty_model() -> None:
    with pytest.raises(LLMConfigError, match="model"):
        BedrockConnector().create_chat_model(" ", {})


def test_langchain_builds_any_provider_model_through_init_chat_model() -> None:
    chat_model = LangChainConnector().create_chat_model(
        "bedrock_converse:placeholder-model-id", {"region_name": "ap-south-1"}
    )
    assert isinstance(chat_model, ChatBedrockConverse)
    assert chat_model.model_id == "placeholder-model-id"


def test_langchain_accepts_the_provider_as_a_param() -> None:
    chat_model = LangChainConnector().create_chat_model(
        "placeholder-model-id", {"model_provider": "bedrock_converse", "region_name": "ap-south-1"}
    )
    assert isinstance(chat_model, ChatBedrockConverse)


def test_langchain_needs_a_provider() -> None:
    with pytest.raises(LLMConfigError, match="provider"):
        LangChainConnector().create_chat_model("placeholder-model-id", {})


def test_langchain_reports_an_unsupported_provider_as_config_error() -> None:
    with pytest.raises(LLMConfigError, match="no-such-provider"):
        LangChainConnector().create_chat_model("no-such-provider:m", {})


def test_langchain_needs_no_environment_of_its_own() -> None:
    assert LangChainConnector.required_env == ()


def test_ping_reports_a_healthy_model() -> None:
    status = ping(FakeListChatModel(responses=["OK"]))
    assert status == HealthStatus(ok=True, detail="OK", latency_ms=status.latency_ms)
    assert status.latency_ms is not None
    assert status.latency_ms >= 0


class _Failing(FakeListChatModel):
    def _call(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("access denied for account 123")


def test_ping_reports_a_failure_by_exception_type_only() -> None:
    status = ping(_Failing(responses=["unused"]))
    assert not status.ok
    assert status.detail == "RuntimeError"


def test_provider_healthchecks_build_the_model_and_ping_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_ping(chat_model: Any) -> HealthStatus:
        calls.append(type(chat_model).__name__)
        return HealthStatus(ok=True, detail="OK")

    monkeypatch.setattr("onb_llm.connectors.bedrock.ping", fake_ping)
    monkeypatch.setattr("onb_llm.connectors.langchain.ping", fake_ping)
    assert BedrockConnector().healthcheck("placeholder-model-id", {}).ok
    assert LangChainConnector().healthcheck("bedrock_converse:placeholder-model-id", {}).ok
    assert calls == ["ChatBedrockConverse", "ChatBedrockConverse"]


def test_a_healthcheck_whose_model_cannot_be_built_is_unhealthy() -> None:
    status = LangChainConnector().healthcheck("placeholder-model-id", {})
    assert not status.ok
    assert status.detail == "LLMConfigError"
