"""`GET /agents`, `GET /agents/{name}`: HR_ADMIN reads the agent registry (REG-1 box 4; PRD U8, FR-A1, §10).

HR and MANAGER get 403 before the registry is read. The registry is global (the same agents serve
every org), so an HR_ADMIN of either org sees the same manifests, and nothing org-specific is in them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry
from onb_api import create_app
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, bearer, token_for
from onb_contracts.auth import Role
from onb_llm import load_llm_config

CONFIG = Path(__file__).resolve().parents[3] / "config"

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_ADMIN = ("u_globex_admin", "org_globex", Role.HR_ADMIN)
SIX = ["approvals_agent", "db_agent", "provisioning_agent", "run_assistant", "template_builder", "templates_agent"]


def _get(client: TestClient, path: str, actor: tuple[str, str, Role]) -> Any:
    return client.get(path, headers=bearer(token_for(*actor)))


# --- box 4: GET /agents is HR_ADMIN only ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/agents", "/agents/db_agent"])
@pytest.mark.parametrize(("actor", "status"), [(ADMIN, 200), (HR, 403), (MANAGER, 403)], ids=["admin", "hr", "mgr"])
def test_the_registry_is_hr_admin_only(
    client: TestClient, path: str, actor: tuple[str, str, Role], status: int
) -> None:
    response = _get(client, path, actor)
    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert response.json()["data"] is None


@pytest.mark.parametrize("path", ["/agents", "/agents/db_agent"])
def test_the_registry_needs_a_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_hr_and_managers_are_refused_before_the_registry_is_read(
    app: FastAPI, client: TestClient, registry: AgentRegistry
) -> None:
    calls: list[str] = []
    original = registry.list_agents

    async def counting() -> Any:
        calls.append("list")
        return await original()

    app.state.agents = type("Spy", (), {"list_agents": staticmethod(counting), "get_agent": registry.get_agent})()
    assert _get(client, "/agents", HR).status_code == 403
    assert _get(client, "/agents", MANAGER).status_code == 403
    assert calls == []
    assert _get(client, "/agents", ADMIN).status_code == 200
    assert calls == ["list"]


# --- what HR_ADMIN sees (U8) ---------------------------------------------------------------------


def test_the_list_shows_each_agent_its_model_and_its_allowed_tools(client: TestClient) -> None:
    body = _get(client, "/agents", ADMIN).json()
    assert body["success"] is True
    agents = body["data"]
    assert [a["name"] for a in agents] == SIX
    db_agent = next(a for a in agents if a["name"] == "db_agent")
    assert db_agent["version"] == 1
    assert db_agent["active"] is True
    assert db_agent["llm"] == {"alias": "standard", "connector": None, "model": None, "params": {}}
    assert db_agent["mcp"]["mongo"]["tools"] == ["find", "aggregate", "count", "update_one"]
    assert db_agent["mcp"]["mongo"]["collections"]["write"] == ["run_tasks", "documents_meta"]
    assert db_agent["limits"] == {"max_steps": 8, "max_tokens": 20000, "timeout_s": 60}
    assert len(db_agent["sha256"]) == 64


def test_one_agent_by_name(client: TestClient) -> None:
    body = _get(client, "/agents/provisioning_agent", ADMIN).json()
    assert body["data"]["name"] == "provisioning_agent"
    assert body["data"]["mcp"]["mocks"] == {
        "tools": ["create_bitbucket_account", "provision_laptop"],
        "collections": None,
    }


def test_an_unknown_agent_is_404(client: TestClient) -> None:
    response = _get(client, "/agents/no_such_agent", ADMIN)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("name", ["DB_AGENT", "db-agent", "x" * 65, "pii vault"])
def test_a_malformed_name_is_a_validation_error(client: TestClient, name: str) -> None:
    response = _get(client, f"/agents/{name}", ADMIN)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_both_orgs_admins_see_the_same_global_registry_and_no_org_data(client: TestClient) -> None:
    acme = _get(client, "/agents", ADMIN).json()["data"]
    globex = _get(client, "/agents", GLOBEX_ADMIN).json()["data"]
    assert acme == globex
    text = str(acme)
    assert "org_acme" not in text
    assert "org_globex" not in text


def test_without_a_registry_the_routes_answer_503() -> None:
    app = create_app(auth=TEST_SETTINGS, audit=InMemoryAuditLog())
    with TestClient(app) as client:
        for path in ("/agents", "/agents/db_agent"):
            response = _get(client, path, ADMIN)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# --- UI-8 / FR-A2: the connector and model each agent runs on -------------------------------------
# Manifests name an alias (`standard`); llm.yaml and the environment turn it into a connector and a
# model. The API resolves it the way the agent runtime does, and shows only the choice, never the
# provider params (region, guardrail, fake script) that llm.yaml routes through.

LLM_ENV = {
    "LLM_CONNECTOR": "fake",
    "LLM_MODEL_FAST": "fake-fast-model",
    "LLM_MODEL_STANDARD": "fake-standard-model",
    "LLM_MODEL_DEEP": "fake-deep-model",
    "AWS_REGION": "region-sentinel-1",
    "BEDROCK_GUARDRAIL_ID": "gr-secret-sentinel",
    "LLM_FAKE_SCRIPT_FILE": "script-sentinel.json",
}


def _llm_app(registry: AgentRegistry, env: dict[str, str] = LLM_ENV) -> FastAPI:
    config = load_llm_config(CONFIG / "llm.yaml", env)
    return create_app(auth=TEST_SETTINGS, agents=registry, llm=config)


def test_each_agent_shows_the_connector_and_model_it_resolves_to(registry: AgentRegistry) -> None:
    with TestClient(_llm_app(registry)) as client:
        agents = _get(client, "/agents", ADMIN).json()["data"]
        one = _get(client, "/agents/db_agent", ADMIN).json()["data"]
    by_name = {a["name"]: a["resolved_llm"] for a in agents}
    assert by_name["db_agent"] == {
        "connector": "fake",
        "model": "fake-standard-model",
        "alias": "standard",
        "source": "manifest",
    }
    assert one["resolved_llm"] == by_name["db_agent"]
    assert {r["connector"] for r in by_name.values()} == {"fake"}


def test_the_resolved_model_follows_the_environment(registry: AgentRegistry) -> None:
    env = {**LLM_ENV, "LLM_CONNECTOR": "bedrock", "LLM_MODEL_STANDARD": "provider.model-standard-v1"}
    with TestClient(_llm_app(registry, env)) as client:
        resolved = _get(client, "/agents/db_agent", ADMIN).json()["data"]["resolved_llm"]
    assert (resolved["connector"], resolved["model"]) == ("bedrock", "provider.model-standard-v1")


def test_provider_params_never_reach_the_response(registry: AgentRegistry) -> None:
    for env in (LLM_ENV, {**LLM_ENV, "LLM_CONNECTOR": "bedrock"}):
        with TestClient(_llm_app(registry, env)) as client:
            text = _get(client, "/agents", ADMIN).text + _get(client, "/agents/db_agent", ADMIN).text
        for sentinel in ("region-sentinel", "gr-secret-sentinel", "script-sentinel", "guardrail", "script_file"):
            assert sentinel not in text


def test_without_an_llm_config_the_resolved_model_is_null(client: TestClient) -> None:
    assert _get(client, "/agents/db_agent", ADMIN).json()["data"]["resolved_llm"] is None
