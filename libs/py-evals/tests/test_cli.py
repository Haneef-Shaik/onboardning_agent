"""`python -m onb_evals run|selftest` (`nx run evals:run`, `make evals-selftest`): arguments, environment,
and the errors a user sees before anything touches a database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from onb_evals.adapters import AdapterNotFoundError, RuntimeLLM, adapter_for, parse_llm, register_adapter
from onb_evals.cli import ENV_MONGO_URI, ENV_RESULTS_DATABASE, ENV_RESULTS_ORG, main, parse_args
from onb_evals.scripted import ScriptedAgent, fake_runtime
from onb_evals.tokens import TokenMap
from onb_telemetry.testing import captured_telemetry

ENV = {
    ENV_MONGO_URI: "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=100",
    ENV_RESULTS_DATABASE: "onb_eval_results",
    ENV_RESULTS_ORG: "org_platform",
}


@pytest.fixture(autouse=True)
def _telemetry() -> Iterator[None]:
    """An active in-memory pipeline, so `main` registers its span hook instead of owning process globals."""
    with captured_telemetry():
        yield


def test_run_takes_agent_llm_and_repeat_in_nx_form() -> None:
    args = parse_args(["run", "--agent=db_agent", "--llm=standard", "--repeat=3"])
    assert (args.command, args.agent, args.llm, args.repeat) == ("run", "db_agent", "standard", 3)


def test_selftest_defaults_to_three_repetitions() -> None:
    assert parse_args(["selftest"]).repeat == 3


@pytest.mark.parametrize("argv", [["run", "--llm", "standard"], ["run", "--agent", "x", "--llm", "s", "--repeat", "0"]])
def test_bad_arguments_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        parse_args(argv)
    assert raised.value.code == 2


@pytest.mark.parametrize("missing", [ENV_MONGO_URI, ENV_RESULTS_DATABASE, ENV_RESULTS_ORG])
def test_a_missing_variable_is_named_and_exits_2(missing: str, capsys: pytest.CaptureFixture[str]) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    assert main(["selftest"], env=env) == 2
    assert missing in capsys.readouterr().err


def test_running_an_agent_without_an_adapter_exits_2_with_the_reason(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            ["run", "--agent", "db_agent", "--llm", "standard"],
            env={
                **ENV,
                "LLM_CONNECTOR": "fake",
                "LLM_MODEL_FAST": "f",
                "LLM_MODEL_STANDARD": "s",
                "LLM_MODEL_DEEP": "d",
            },
        )
        == 2
    )
    assert "no eval adapter is registered for agent 'db_agent'" in capsys.readouterr().err


def test_an_agent_with_no_cases_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    env = {**ENV, "LLM_CONNECTOR": "fake", "LLM_MODEL_FAST": "f", "LLM_MODEL_STANDARD": "s", "LLM_MODEL_DEEP": "d"}
    assert main(["run", "--agent", "nobody", "--llm", "standard"], env=env) == 2
    assert "no cases for agent 'nobody'" in capsys.readouterr().err


def test_parse_llm_reads_an_alias_or_connector_and_model() -> None:
    assert parse_llm("standard").alias == "standard"
    explicit = parse_llm("fake:any-model")
    assert (explicit.connector, explicit.model) == ("fake", "any-model")


def test_runtime_llm_resolves_through_llm_1_and_attaches_the_capture() -> None:
    provider = RuntimeLLM(fake_runtime(), "standard")
    assert provider.label == "fake:fake-standard"
    marker = BaseCallbackHandler()
    model = provider.chat_model([marker], TokenMap({}))
    assert isinstance(model.callbacks, list)
    assert marker in model.callbacks


def test_the_adapter_registry_refuses_duplicates_and_finds_registered_agents() -> None:
    register_adapter("test_only_agent", ScriptedAgent)
    assert isinstance(adapter_for("test_only_agent"), ScriptedAgent)
    with pytest.raises(ValueError, match="already registered"):
        register_adapter("test_only_agent", ScriptedAgent)
    with pytest.raises(AdapterNotFoundError):
        adapter_for("never_registered")
