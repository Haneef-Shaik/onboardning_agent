"""`config/llm.yaml` loading: `${ENV}` interpolation and the shape of a model selection."""

from collections.abc import Callable
from pathlib import Path

import pytest
from onb_llm import LLMConfigError, LLMSelection, interpolate, load_llm_config
from pydantic import ValidationError

ConfigWriter = Callable[[str], Path]
REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "llm.yaml"


def test_interpolation_substitutes_set_variables() -> None:
    assert interpolate("${A}-${B}", {"A": "x", "B": "y"}, where="t") == "x-y"


def test_interpolation_uses_the_default_when_the_variable_is_unset_or_empty() -> None:
    assert interpolate("${A:-d}", {}, where="t") == "d"
    assert interpolate("${A:-d}", {"A": ""}, where="t") == "d"
    assert interpolate("${A:-}", {}, where="t") == ""


@pytest.mark.parametrize("env", [{}, {"MODEL_X": ""}, {"MODEL_X": "   "}])
def test_interpolation_of_an_unset_or_empty_variable_fails_naming_it_and_where(env: dict[str, str]) -> None:
    with pytest.raises(LLMConfigError, match=r"MODEL_X.*aliases\.fast\.model|aliases\.fast\.model.*MODEL_X"):
        interpolate("${MODEL_X}", env, where="aliases.fast.model")


def test_text_without_placeholders_is_unchanged() -> None:
    assert interpolate("plain $text", {}, where="t") == "plain $text"


def test_load_interpolates_every_string_in_the_file(write_config: ConfigWriter) -> None:
    path = write_config(
        """
        default: { alias: standard }
        aliases:
          standard: { connector: "${CONN:-fake}", model: "${M}", params: { temperature: 0 } }
        connectors:
          fake: { params: { note: "${NOTE:-none}" } }
        """
    )
    config = load_llm_config(path, {"M": "m-1"})
    assert config.aliases["standard"] == LLMSelection(connector="fake", model="m-1", params={"temperature": 0})
    assert config.connectors["fake"].params == {"note": "none"}


def test_load_reports_the_file_and_the_variable(write_config: ConfigWriter) -> None:
    path = write_config("default: { connector: fake, model: '${LLM_MODEL_STANDARD}' }\n")
    with pytest.raises(LLMConfigError) as caught:
        load_llm_config(path, {})
    message = str(caught.value)
    assert "LLM_MODEL_STANDARD" in message
    assert str(path) in message
    assert "default.model" in message


def test_a_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError, match="not found"):
        load_llm_config(tmp_path / "absent.yaml", {})


@pytest.mark.parametrize(
    "text",
    [
        "- just\n- a list\n",
        "default: [unclosed\n",
        "aliases: {}\n",
        "default: { alias: nope }\naliases: {}\n",
        "default: { alias: a }\naliases: { a: { alias: b }, b: { connector: fake, model: m } }\n",
        "default: { connector: fake, model: m }\nsurprise: 1\n",
    ],
    ids=["not-a-mapping", "bad-yaml", "no-default", "default-alias-unknown", "alias-of-alias", "unknown-key"],
)
def test_malformed_files_are_rejected(write_config: ConfigWriter, text: str) -> None:
    with pytest.raises(LLMConfigError):
        load_llm_config(write_config(text), {})


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"alias": "fast", "connector": "fake"},
        {"alias": "fast", "model": "m"},
        {"connector": "fake"},
        {"model": "m"},
        {"connector": " ", "model": "m"},
        {"connector": "fake", "model": "m", "surprise": 1},
    ],
)
def test_a_selection_is_either_an_alias_or_a_connector_and_model(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LLMSelection.model_validate(fields)


def test_selections_are_immutable() -> None:
    selection = LLMSelection(alias="fast")
    with pytest.raises(ValidationError):
        selection.alias = "deep"


def test_the_repository_config_names_the_three_aliases_and_takes_models_from_env() -> None:
    env = {"LLM_MODEL_FAST": "f", "LLM_MODEL_STANDARD": "s", "LLM_MODEL_DEEP": "d"}
    config = load_llm_config(REPO_CONFIG, env)
    assert set(config.aliases) == {"fast", "standard", "deep"}
    assert {a: s.model for a, s in config.aliases.items()} == {"fast": "f", "standard": "s", "deep": "d"}
    assert {s.connector for s in config.aliases.values()} == {"bedrock"}
    assert config.default == LLMSelection(alias="standard")


def test_the_repository_config_lets_env_switch_the_connector() -> None:
    env = {"LLM_CONNECTOR": "fake", "LLM_MODEL_FAST": "f", "LLM_MODEL_STANDARD": "s", "LLM_MODEL_DEEP": "d"}
    config = load_llm_config(REPO_CONFIG, env)
    assert {s.connector for s in config.aliases.values()} == {"fake"}


def test_the_repository_config_fails_without_model_env() -> None:
    with pytest.raises(LLMConfigError, match="LLM_MODEL_"):
        load_llm_config(REPO_CONFIG, {})
