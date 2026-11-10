"""Resolution order (PRD §7.1): agent manifest → org override → global default."""

import pytest
from onb_llm import LLMConfig, LLMConfigError, LLMSelection, ResolvedLLM, resolve_llm

CONFIG = LLMConfig.model_validate(
    {
        "default": {"alias": "standard"},
        "aliases": {
            "fast": {"connector": "fake", "model": "model-fast", "params": {"temperature": 0.5}},
            "standard": {"connector": "fake", "model": "model-standard"},
            "deep": {"connector": "other", "model": "model-deep"},
        },
        "connectors": {"fake": {"params": {"temperature": 0.0, "max_tokens": 100}}},
    }
)


def test_manifest_wins_over_org_override_and_default() -> None:
    resolved = resolve_llm(
        CONFIG,
        manifest=LLMSelection(alias="deep"),
        org_override=LLMSelection(alias="fast"),
    )
    assert resolved.source == "manifest"
    assert (resolved.connector, resolved.model, resolved.alias) == ("other", "model-deep", "deep")


def test_org_override_applies_when_the_manifest_names_no_model() -> None:
    resolved = resolve_llm(CONFIG, manifest=None, org_override=LLMSelection(alias="fast"))
    assert resolved.source == "org_override"
    assert (resolved.connector, resolved.model, resolved.alias) == ("fake", "model-fast", "fast")


def test_global_default_applies_when_neither_manifest_nor_org_names_a_model() -> None:
    resolved = resolve_llm(CONFIG)
    assert resolved.source == "default"
    assert (resolved.connector, resolved.model, resolved.alias) == ("fake", "model-standard", "standard")


def test_an_explicit_connector_and_model_resolve_without_an_alias() -> None:
    manifest = LLMSelection(connector="langchain", model="provider:some-model", params={"temperature": 1})
    resolved = resolve_llm(CONFIG, manifest=manifest)
    assert resolved == ResolvedLLM(
        connector="langchain", model="provider:some-model", params={"temperature": 1}, source="manifest", alias=None
    )


def test_params_layer_connector_then_alias_then_selection() -> None:
    resolved = resolve_llm(CONFIG, manifest=LLMSelection(alias="fast", params={"max_tokens": 7}))
    assert resolved.params == {"temperature": 0.5, "max_tokens": 7}


def test_resolution_leaves_the_config_unchanged() -> None:
    before = CONFIG.model_dump()
    resolve_llm(CONFIG, manifest=LLMSelection(alias="fast", params={"max_tokens": 7}))
    assert CONFIG.model_dump() == before


def test_an_unknown_alias_fails_naming_the_known_ones() -> None:
    with pytest.raises(LLMConfigError, match=r"unknown model alias 'huge'.*deep, fast, standard"):
        resolve_llm(CONFIG, org_override=LLMSelection(alias="huge"))


@pytest.mark.parametrize("key", ["guardrail_id", "guardrail_version", "endpoint_url", "base_url", "script_file"])
@pytest.mark.parametrize("source", ["manifest", "org_override"])
def test_a_manifest_or_org_override_may_only_set_sampling_params(source: str, key: str) -> None:
    # Provider wiring (guardrail, endpoint, credentials, scripts) is set only in llm.yaml, so an org
    # override cannot switch Bedrock Guardrails off or point the model at another host.
    selection = LLMSelection(alias="fast", params={key: "x"})
    with pytest.raises(LLMConfigError, match=rf"{source}.*'{key}'.*max_tokens"):
        resolve_llm(CONFIG, **{source: selection})


def test_the_task_record_names_the_model_but_holds_no_params() -> None:
    resolved = resolve_llm(CONFIG, manifest=LLMSelection(alias="fast"))
    assert resolved.task_record() == {"connector": "fake", "model": "model-fast", "alias": "fast", "source": "manifest"}
