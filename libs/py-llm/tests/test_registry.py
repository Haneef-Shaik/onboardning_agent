"""Connector discovery through the `onboarding.llm_connectors` entry-point group (PRD §7.1)."""

from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from onb_llm import (
    ENTRY_POINT_GROUP,
    ConnectorNotFoundError,
    ConnectorRegistry,
    ConnectorRegistryError,
    LLMConfig,
    LLMRuntime,
    LLMSelection,
    discover_connectors,
)
from onb_llm.connectors.fake import FakeConnector

CONNECTOR_PKG = Path(__file__).parent / "connector_pkg"


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)


def test_the_built_in_connectors_are_found_by_entry_point() -> None:
    registry = discover_connectors()
    assert {"bedrock", "langchain", "fake"} <= set(registry.names())
    assert registry.get("fake").name == "fake"


def test_a_connector_package_registered_by_entry_point_is_discovered_with_no_core_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "echo" not in discover_connectors().names()

    monkeypatch.syspath_prepend(str(CONNECTOR_PKG))  # what installing the package does

    registry = discover_connectors()
    assert "echo" in registry.names()
    assert type(registry.get("echo")).__module__ == "onb_llm_echo_connector"

    config = LLMConfig.model_validate({"default": {"connector": "fake", "model": "unused"}})
    runtime = LLMRuntime.start(config, registry, env={})
    chat_model = runtime.chat_model(runtime.resolve(manifest=LLMSelection(connector="echo", model="echo-1")))
    assert isinstance(chat_model, BaseChatModel)
    assert chat_model.invoke("hi").content == "echo from echo-1"


def test_an_unknown_connector_fails_naming_the_installed_ones() -> None:
    registry = ConnectorRegistry.of(FakeConnector())
    with pytest.raises(ConnectorNotFoundError, match=r"'nope'.*installed: fake"):
        registry.get("nope")


def test_an_entry_point_that_fails_to_load_is_reported_by_name() -> None:
    with pytest.raises(ConnectorRegistryError, match=r"'broken'.*no_such_module_for_onb_llm"):
        discover_connectors([_ep("broken", "no_such_module_for_onb_llm:Connector")])


def test_an_entry_point_that_is_not_a_connector_is_rejected() -> None:
    with pytest.raises(ConnectorRegistryError, match=r"'notone'.*LLMConnector"):
        discover_connectors([_ep("notone", "builtins:dict")])


def test_an_entry_point_name_must_match_the_connector_name() -> None:
    with pytest.raises(ConnectorRegistryError, match=r"'alias'.*'fake'"):
        discover_connectors([_ep("alias", "onb_llm.connectors.fake:FakeConnector")])


def test_two_packages_claiming_one_name_are_rejected() -> None:
    entry = _ep("fake", "onb_llm.connectors.fake:FakeConnector")
    with pytest.raises(ConnectorRegistryError, match=r"'fake'.*more than once"):
        discover_connectors([entry, entry])


def test_registry_of_rejects_duplicates() -> None:
    with pytest.raises(ConnectorRegistryError, match="more than once"):
        ConnectorRegistry.of(FakeConnector(), FakeConnector())
