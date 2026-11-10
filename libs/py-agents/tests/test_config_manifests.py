"""The shipped manifests and tool catalog (REG-1 box 3; PRD §7.3, FR-A1).

All six agents' manifests in `config/agents/` validate, and `config/tool_catalog.yaml` is exactly the
eval fixture's `action_catalog`, so templates, evals and the registry agree on who does what.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from onb_agents.registry import ManifestError, load_catalog, load_manifests, parse_catalog
from onb_agents.registry.manifest import MONGO_SERVER, MONGO_WRITE_TOOLS, SELECTION_PARAMS, ManifestLLM

ROOT = Path(__file__).resolve().parents[3]
AGENTS = ROOT / "config" / "agents"
CATALOG = ROOT / "config" / "tool_catalog.yaml"
FIXTURE = ROOT / "evals" / "fixtures" / "seed_v1.json"
EVAL_CASES = ROOT / "evals" / "datasets" / "onboarding_agents_v1.jsonl"

SIX_AGENTS = {
    "template_builder",
    "approvals_agent",
    "db_agent",
    "templates_agent",
    "provisioning_agent",
    "run_assistant",
}


def test_all_six_manifests_validate() -> None:
    manifests = load_manifests(AGENTS)
    assert {m.name for m in manifests} == SIX_AGENTS
    assert len(manifests) == 6


def test_the_catalog_matches_the_eval_fixture_action_catalog() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["action_catalog"]
    catalog = load_catalog(CATALOG, load_manifests(AGENTS))
    assert catalog.entries() == fixture


def test_every_eval_case_agent_has_a_manifest() -> None:
    agents = {json.loads(line)["agent"] for line in EVAL_CASES.read_text(encoding="utf-8").splitlines() if line}
    assert agents <= SIX_AGENTS


def test_only_the_db_agent_may_write_and_never_the_hire_record() -> None:
    """Least privilege (PRD §5.2): the agents whose eval cases forbid writes hold no write tool."""
    writers = {
        m.name: m.mcp[MONGO_SERVER].collections
        for m in load_manifests(AGENTS)
        if MONGO_SERVER in m.mcp and set(m.mcp[MONGO_SERVER].tools) & MONGO_WRITE_TOOLS
    }
    assert set(writers) == {"db_agent"}
    db_collections = writers["db_agent"]
    assert db_collections is not None
    assert "new_hires" not in db_collections.write


def test_tools_the_eval_cases_forbid_are_not_granted() -> None:
    granted = {
        m.name: {tool for grant in m.mcp.values() for tool in grant.tools} | set(m.tools)
        for m in load_manifests(AGENTS)
    }
    assert "publish_template" not in granted["template_builder"]
    assert "start_run" not in granted["template_builder"]
    assert "record_approval_decision" not in granted["approvals_agent"]
    assert not {"retry_task", "complete_task"} & granted["run_assistant"]


def test_the_manifest_llm_has_the_shape_of_the_llm_selection() -> None:
    from onb_llm import LLMSelection
    from onb_llm.resolution import SELECTION_PARAMS as LLM_SELECTION_PARAMS

    assert set(ManifestLLM.model_fields) == set(LLMSelection.model_fields)
    assert SELECTION_PARAMS == LLM_SELECTION_PARAMS
    for manifest in load_manifests(AGENTS):
        LLMSelection.model_validate(manifest.llm.model_dump())


# --- loader and catalog errors -------------------------------------------------------------------


def _copy_agents(tmp_path: Path) -> Path:
    target = tmp_path / "agents"
    shutil.copytree(AGENTS, target)
    return target


def test_a_file_named_after_another_agent_is_refused(tmp_path: Path) -> None:
    agents = _copy_agents(tmp_path)
    (agents / "db_agent.yaml").rename(agents / "db.yaml")
    with pytest.raises(ManifestError, match=r"db\.yaml: names the agent db_agent"):
        load_manifests(agents)


def test_one_bad_manifest_fails_the_load_and_names_the_file(tmp_path: Path) -> None:
    agents = _copy_agents(tmp_path)
    path = agents / "run_assistant.yaml"
    path.write_text(path.read_text().replace("knowledge_docs]", "knowledge_docs, pii_vault]"))
    with pytest.raises(ManifestError, match=r"run_assistant\.yaml: .*vault"):
        load_manifests(agents)


def test_unreadable_yaml_is_refused_without_echoing_it(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "x.yaml").write_text("name: [unclosed\n")
    with pytest.raises(ManifestError, match=r"x\.yaml: cannot read it as YAML") as caught:
        load_manifests(agents)
    assert "unclosed" not in str(caught.value)


def test_an_empty_or_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no agent manifests"):
        load_manifests(tmp_path)
    with pytest.raises(ManifestError, match="not a directory"):
        load_manifests(tmp_path / "missing")


@pytest.mark.parametrize(
    "data,message",
    [
        (["x"], "mapping"),
        ({"actions": []}, "actions"),
        ({"actions": [{"action": "approval", "agent": "nobody"}]}, "without a manifest: nobody"),
        ({"actions": [{"action": "a", "agent": "db_agent"}, {"action": "a", "agent": "db_agent"}]}, "repeats a"),
        ({"actions": [{"action": "read_vault", "agent": "db_agent", "task_type": "pii_vault"}]}, "vault"),
        ({"actions": [{"action": "Approval", "agent": "db_agent"}]}, "action"),
    ],
)
def test_a_bad_catalog_is_refused(data: object, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse_catalog(data, load_manifests(AGENTS))


def test_a_catalog_file_error_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "tool_catalog.yaml"
    path.write_text("actions: []\n")
    with pytest.raises(ManifestError, match=r"^tool_catalog\.yaml: "):
        load_catalog(path, load_manifests(AGENTS))


def test_the_catalog_finds_the_agent_for_an_action() -> None:
    catalog = load_catalog(CATALOG, load_manifests(AGENTS))
    assert catalog.agent_for("create_bitbucket_account") == "provisioning_agent"
    assert catalog.agent_for("unknown") is None
