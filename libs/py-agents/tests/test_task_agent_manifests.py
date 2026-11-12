"""AGT-1: the DB, Templates and Provisioning agents' manifests cover what their eval trajectories do.

EVAL-1's self-test scripts one trajectory per case (`evals/selftest/scripted_outputs_v1.jsonl`). A real
model attempting the same steps is stopped by the hub when the manifest does not grant them, so each
tool call and collection a `db-*`, `tp-*` case's trajectory uses must be inside its agent's manifest
(PRD §7.3). A case that expects a denial (a safety case) names the call in its own checks instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from onb_agents.registry import AgentManifest, load_manifests
from onb_agents.registry.manifest import MONGO_SERVER, MONGO_WRITE_TOOLS

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "evals" / "datasets" / "onboarding_agents_v1.jsonl"
SCRIPTS = ROOT / "evals" / "selftest" / "scripted_outputs_v1.jsonl"
PREFIXES = ("db-", "tp-")
# Cases whose script deliberately attempts a call the platform must refuse; db-004's `$where` filter is
# refused by the hub's argument validation, not by the manifest.
EXPECTED_DENIALS = {"db-004"}


def _manifests() -> dict[str, AgentManifest]:
    return {m.name: m for m in load_manifests(ROOT / "config" / "agents")}


def _agent_of(case_id: str) -> str:
    for line in CASES.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["id"] == case_id:
            return str(case["agent"])
    raise AssertionError(case_id)


def _calls() -> list[tuple[str, str, str, str | None]]:
    """(case, agent, tool, collection) for every scripted tool call of the AGT-1 cases."""
    found = []
    for line in SCRIPTS.read_text(encoding="utf-8").splitlines():
        script = json.loads(line)
        case = script["case_id"]
        if not case.startswith(PREFIXES):
            continue
        for turn in script["turns"]:
            for call in turn.get("tool_calls", []):
                found.append((case, _agent_of(case), call["name"], call["args"].get("collection")))
    return found


def test_there_is_a_scripted_trajectory_for_every_task_agent_case() -> None:
    cases = {c for c, *_ in _calls()}
    assert {"db-001", "db-002", "db-006", "tp-001", "tp-002", "tp-003"} <= cases


def test_every_scripted_tool_call_is_granted_by_the_agents_manifest() -> None:
    manifests = _manifests()
    missing = []
    for case, agent, tool, collection in _calls():
        if case in EXPECTED_DENIALS:
            continue
        manifest = manifests[agent]
        granted = {t for grant in manifest.mcp.values() for t in grant.tools}
        if tool not in granted:
            missing.append(f"{case}: {agent} has no tool {tool}")
            continue
        if collection is None:
            continue
        grant = manifest.mcp[MONGO_SERVER].collections
        assert grant is not None
        allowed = grant.write if tool in MONGO_WRITE_TOOLS else grant.read
        if collection not in allowed:
            missing.append(f"{case}: {agent} may not {tool} {collection}")
    assert missing == []


def test_the_templates_agent_reads_hires_but_writes_nothing() -> None:
    manifest = _manifests()["templates_agent"]
    grant = manifest.mcp[MONGO_SERVER].collections
    assert grant is not None
    assert "new_hires" in grant.read
    assert grant.write == ()
    assert not set(manifest.mcp[MONGO_SERVER].tools) & MONGO_WRITE_TOOLS


def test_the_provisioning_agent_reaches_only_the_two_mock_tools_and_reads_run_tasks() -> None:
    manifest = _manifests()["provisioning_agent"]
    assert set(manifest.mcp) == {MONGO_SERVER, "mocks"}
    assert set(manifest.mcp["mocks"].tools) == {"create_bitbucket_account", "provision_laptop"}
    grant = manifest.mcp[MONGO_SERVER].collections
    assert grant is not None and grant.read == ("run_tasks",) and grant.write == ()


def test_the_db_agent_never_writes_the_hire_record() -> None:
    grant = _manifests()["db_agent"].mcp[MONGO_SERVER].collections
    assert grant is not None
    assert "new_hires" not in grant.write
    assert grant.write == ("run_tasks", "documents_meta")  # REG-1's stricter version: not widened here
