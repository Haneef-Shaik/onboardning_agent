"""Wiring the agent executor into the worker (RUN-3): settings that fail fast, and what is built."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from pathlib import Path

import pytest
from onb_agents.runs import CLEANUP_RUN, FAIL_TASK, RESUME_RUN, START_RUN
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_crypto import GrantSigningKey
from onb_orchestrator.agent_executor import AgentTaskExecutor
from onb_orchestrator.agents_setup import DEFAULT_RUN_MAX_TOKENS, AgentSettings, build_agent_executor
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.worker import ConfigError, Settings, prepare

ROOT = Path(__file__).resolve().parents[3]
BASE = {"MONGO_URI": "mongodb://x", "MONGO_DATABASE": "onb"}


def agent_env(**overrides: str) -> dict[str, str]:
    env = {
        "CONFIG_DIR": str(ROOT / "config"),
        "AGENT_TOKEN_KEY": secrets.token_urlsafe(40),
        "MCP_HUB_URL": "http://mcp-hub:8100/mcp",
        "PII_GRANT_SIGNING_KEY": GrantSigningKey.generate().encoded(),
        "LLM_CONNECTOR": "fake",
        "LLM_MODEL_FAST": "m1",
        "LLM_MODEL_STANDARD": "m2",
        "LLM_MODEL_DEEP": "m3",
    }
    return {**env, **overrides}


def test_agent_settings_come_from_the_environment() -> None:
    env = agent_env()
    settings = AgentSettings.from_env(env)
    assert settings.hub_url == "http://mcp-hub:8100/mcp"
    assert settings.run_max_tokens == DEFAULT_RUN_MAX_TOKENS
    assert settings.policy_file == ROOT / "config" / "pii_sink_policy.yaml"
    assert AgentSettings.from_env({**env, "ORCHESTRATOR_RUN_MAX_TOKENS": "5000"}).run_max_tokens == 5000


@pytest.mark.parametrize(
    "broken",
    [
        {"CONFIG_DIR": ""},
        {"AGENT_TOKEN_KEY": ""},
        {"AGENT_TOKEN_KEY": "short"},
        {"MCP_HUB_URL": ""},
        {"MCP_HUB_URL": "not a url"},
        {"PII_GRANT_SIGNING_KEY": ""},
        {"PII_GRANT_SIGNING_KEY": "not base64 of 32 bytes"},
        {"ORCHESTRATOR_RUN_MAX_TOKENS": "0"},
        {"ORCHESTRATOR_RUN_MAX_TOKENS": "many"},
        {"PII_SINK_POLICY_FILE": "/no/such/policy.yaml"},
    ],
)
def test_agent_settings_fail_fast_naming_the_variable_never_its_value(broken: Mapping[str, str]) -> None:
    with pytest.raises(ConfigError) as caught:
        AgentSettings.from_env(agent_env(**broken))
    name = next(iter(broken))
    assert name in str(caught.value)
    assert next(iter(broken.values())) not in str(caught.value) or not next(iter(broken.values()))


def test_secrets_are_never_in_the_settings_repr() -> None:
    env = agent_env()
    rendered = repr(AgentSettings.from_env(env))
    assert env["AGENT_TOKEN_KEY"] not in rendered
    assert env["PII_GRANT_SIGNING_KEY"] not in rendered


def test_the_worker_runs_the_stub_unless_the_agents_executor_is_chosen() -> None:
    assert Settings.from_env(BASE).agents is None
    assert Settings.from_env({**BASE, "ORCHESTRATOR_EXECUTOR": "stub"}).agents is None
    chosen = Settings.from_env({**BASE, **agent_env(), "ORCHESTRATOR_EXECUTOR": "agents"})
    assert chosen.agents is not None


def test_choosing_the_agents_executor_without_its_settings_fails_fast() -> None:
    with pytest.raises(ConfigError, match="CONFIG_DIR"):
        Settings.from_env({**BASE, "ORCHESTRATOR_EXECUTOR": "agents"})
    with pytest.raises(ConfigError, match="AGENT_TOKEN_KEY"):
        Settings.from_env({**BASE, **agent_env(AGENT_TOKEN_KEY=""), "ORCHESTRATOR_EXECUTOR": "agents"})
    with pytest.raises(ConfigError, match="ORCHESTRATOR_EXECUTOR"):
        Settings.from_env({**BASE, "ORCHESTRATOR_EXECUTOR": "magic"})


def test_build_agent_executor_makes_an_agent_task_executor_on_the_fake_connector() -> None:
    env = agent_env()
    executor = build_agent_executor(memory_store(MemoryDatabase()), AgentSettings.from_env(env), env)
    assert isinstance(executor, AgentTaskExecutor)


def test_prepare_subscribes_the_start_run_and_fail_task_consumers() -> None:
    worker = asyncio.run(prepare(memory_store(MemoryDatabase()), stub_delay=0.0))
    assert worker.consumers == (START_RUN, RESUME_RUN, CLEANUP_RUN, FAIL_TASK)  # RUN-2 adds resume + cleanup


def test_prepare_uses_the_executor_it_is_given() -> None:
    given = StubExecutor()
    worker = asyncio.run(prepare(memory_store(MemoryDatabase()), stub_delay=0.0, executor=given))
    assert worker.consumers == (START_RUN, RESUME_RUN, CLEANUP_RUN, FAIL_TASK)
