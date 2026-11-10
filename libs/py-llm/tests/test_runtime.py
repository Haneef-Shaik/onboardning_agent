"""Startup validation (fail fast on missing env) and chat-model creation with LLM spans."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from onb_llm import (
    ConnectorEnvironmentError,
    ConnectorNotFoundError,
    ConnectorRegistry,
    HealthStatus,
    LLMConfig,
    LLMRuntime,
    LLMSelection,
    discover_connectors,
    load_llm_config,
)
from onb_llm.connectors.fake import FakeConnector
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry.trace import SpanKind, StatusCode

ConfigWriter = Callable[[str], Path]
REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "llm.yaml"
MODEL_ENV = {"LLM_MODEL_FAST": "model-f", "LLM_MODEL_STANDARD": "model-s", "LLM_MODEL_DEEP": "model-d"}


def _fake_config(**params: object) -> LLMConfig:
    return LLMConfig.model_validate({"default": {"connector": "fake", "model": "scripted", "params": params}})


def test_selecting_bedrock_without_aws_region_fails_at_startup_with_a_clear_message() -> None:
    config = load_llm_config(REPO_CONFIG, MODEL_ENV)  # LLM_CONNECTOR unset: bedrock is selected

    with pytest.raises(ConnectorEnvironmentError) as caught:
        LLMRuntime.start(config, discover_connectors(), env=MODEL_ENV)

    message = str(caught.value)
    assert "'bedrock'" in message
    assert "AWS_REGION" in message
    assert "default" in message
    assert ".env.example" in message
    assert caught.value.connector == "bedrock"
    assert caught.value.missing == ("AWS_REGION",)


def test_bedrock_starts_when_aws_region_is_set() -> None:
    config = load_llm_config(REPO_CONFIG, MODEL_ENV)
    runtime = LLMRuntime.start(config, discover_connectors(), env={**MODEL_ENV, "AWS_REGION": "ap-south-1"})
    assert runtime.resolve().connector == "bedrock"


def test_a_blank_required_variable_counts_as_missing() -> None:
    config = load_llm_config(REPO_CONFIG, MODEL_ENV)
    with pytest.raises(ConnectorEnvironmentError, match="AWS_REGION"):
        LLMRuntime.start(config, discover_connectors(), env={**MODEL_ENV, "AWS_REGION": "  "})


def test_an_alias_naming_an_uninstalled_connector_fails_at_startup() -> None:
    config = LLMConfig.model_validate(
        {
            "default": {"connector": "fake", "model": "m"},
            "aliases": {"deep": {"connector": "ghost", "model": "m"}},
        }
    )
    with pytest.raises(ConnectorNotFoundError, match=r"'ghost'.*aliases\.deep"):
        LLMRuntime.start(config, discover_connectors(), env={})


def test_a_manifest_selecting_bedrock_is_checked_when_resolved() -> None:
    runtime = LLMRuntime.start(_fake_config(), discover_connectors(), env={})
    with pytest.raises(ConnectorEnvironmentError, match="AWS_REGION"):
        runtime.resolve(manifest=LLMSelection(connector="bedrock", model="model-x"))


def test_the_runtime_does_not_read_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    config = load_llm_config(REPO_CONFIG, MODEL_ENV)
    with pytest.raises(ConnectorEnvironmentError):
        LLMRuntime.start(config, discover_connectors(), env=MODEL_ENV)


def test_chat_model_calls_produce_an_llm_span_with_connector_model_and_tokens(telemetry: CapturedTelemetry) -> None:
    script = [{"content": "hello", "usage": {"input_tokens": 11, "output_tokens": 3}}]
    runtime = LLMRuntime.start(_fake_config(script=script), discover_connectors(), env={})

    reply = runtime.chat_model(runtime.resolve()).invoke([HumanMessage("hi")])

    assert reply.content == "hello"
    (span,) = [s for s in telemetry.spans() if s.name == "llm.chat"]
    assert span.kind is SpanKind.CLIENT
    assert dict(span.attributes or {}) == {
        "llm.connector": "fake",
        "llm.model": "scripted",
        "llm.usage.input_tokens": 11,
        "llm.usage.output_tokens": 3,
        "llm.usage.total_tokens": 14,
    }
    assert span.status.status_code is StatusCode.UNSET


def test_the_llm_span_holds_no_prompt_or_completion_text(telemetry: CapturedTelemetry) -> None:
    runtime = LLMRuntime.start(_fake_config(script=[{"content": "secret reply"}]), discover_connectors(), env={})
    runtime.chat_model(runtime.resolve()).invoke([HumanMessage("secret prompt")])
    (span,) = [s for s in telemetry.spans() if s.name == "llm.chat"]
    exported = repr(dict(span.attributes or {})) + repr([e.attributes for e in span.events])
    assert "secret" not in exported


def test_a_failed_call_ends_its_span_as_an_error_without_the_message(telemetry: CapturedTelemetry) -> None:
    runtime = LLMRuntime.start(_fake_config(script=[]), discover_connectors(), env={})
    with pytest.raises(Exception, match="script"):
        runtime.chat_model(runtime.resolve()).invoke([HumanMessage("hi")])
    (span,) = [s for s in telemetry.spans() if s.name == "llm.chat"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ScriptExhaustedError"
    assert span.attributes is not None
    assert span.attributes["error.type"] == "ScriptExhaustedError"


def test_the_returned_model_is_a_new_object_each_time() -> None:
    runtime = LLMRuntime.start(_fake_config(), discover_connectors(), env={})
    resolved = runtime.resolve()
    assert runtime.chat_model(resolved) is not runtime.chat_model(resolved)


def test_chat_model_for_resolves_and_creates_in_one_step(telemetry: CapturedTelemetry) -> None:
    runtime = LLMRuntime.start(_fake_config(script=[{"content": "ok"}]), discover_connectors(), env={})
    resolved, chat_model = runtime.chat_model_for(org_override=None)
    assert resolved.source == "default"
    assert chat_model.invoke("hi").content == "ok"


class _Recorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.ended = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.ended += 1


class _CallbackConnector:
    """A connector whose model arrives with its own callbacks, as a list or as a manager."""

    name = "withcallbacks"
    required_env: tuple[str, ...] = ()

    def __init__(self, callbacks: Any) -> None:
        self._callbacks = callbacks

    def create_chat_model(self, model: str, params: Mapping[str, Any]) -> BaseChatModel:
        return FakeListChatModel(responses=["hi"], callbacks=self._callbacks)

    def healthcheck(self, model: str, params: Mapping[str, Any]) -> HealthStatus:
        return HealthStatus(ok=True, detail="OK")


@pytest.mark.parametrize("as_manager", [False, True], ids=["list", "manager"])
def test_the_connectors_own_callbacks_are_kept_next_to_the_span_handler(
    telemetry: CapturedTelemetry, as_manager: bool
) -> None:
    recorder = _Recorder()
    callbacks: Any = CallbackManager([recorder]) if as_manager else [recorder]
    config = LLMConfig.model_validate({"default": {"connector": "withcallbacks", "model": "m"}})
    runtime = LLMRuntime.start(config, ConnectorRegistry.of(_CallbackConnector(callbacks)), env={})

    runtime.chat_model(runtime.resolve()).invoke("hello")

    assert recorder.ended == 1
    assert [s.name for s in telemetry.spans()] == ["llm.chat"]


def test_runtime_exposes_its_config_and_registry() -> None:
    config = _fake_config()
    registry = discover_connectors()
    runtime = LLMRuntime.start(config, registry, env={})
    assert runtime.config is config
    assert runtime.registry is registry
    assert runtime.connector("fake").name == FakeConnector.name
