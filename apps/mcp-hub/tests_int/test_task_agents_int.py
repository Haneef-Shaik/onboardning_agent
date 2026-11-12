"""AGT-1 against the real stack (Done-when boxes 2 and 3): tasks of a run execute their registered agents
through the hub, the mocks, the notify sink and MongoDB. Only the model is scripted (no model call).

Box 2: a provisioning task completes through the mocks.
Box 3: the seed template (`templates/engineer-india.yaml`) runs; every task that needs no approval
completes without human action, and the count is printed and recorded in the handoff.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from onb_contracts.templates import Template
from onb_data import ActorType, AuditOutcome
from onb_orchestrator.agent_input import opaque_ref

from .client import agent_claims, call, new_run, text, trail
from .conftest import ROOT, Stack
from .task_agents_support import HIRE, ORG, Platform, on_platform, one_task_template

PROVISIONING_SCOPES = ("mongo__find", "mocks__create_bitbucket_account", "mocks__provision_laptop")


def _run_one(
    stack: Stack, tmp_path: Path, agent: str, action: str, role: str = "engineer"
) -> tuple[str, dict[str, Any]]:
    async def body(p: Platform) -> tuple[str, dict[str, Any]]:
        run_id = await p.start(one_task_template(agent, action), role)
        await p.drive(run_id)
        return run_id, (await p.tasks(run_id))["t1"]

    return on_platform(stack, tmp_path, body, role=role)


def test_a_provisioning_task_creates_the_laptop_order_through_the_mocks(stack: Stack, tmp_path: Path) -> None:
    before = stack.mocks_store.creations
    run_id, task = _run_one(stack, tmp_path, "provisioning_agent", "provision_laptop")
    assert task["status"] == "done", task.get("failure")
    assert task["result"]["outcome"] == "completed"
    assert task["result"]["output"]["provisioned"] == [{"action": "provision_laptop", "done": True}]
    assert stack.mocks_store.creations == before + 1
    found = trail(stack, run_id, ORG)
    assert (found.attrs["outcome"], found.attrs["agent.name"], found.attrs["mcp.server"]) == (
        "ok",
        "provisioning_agent",
        "mocks",
    )
    [record] = found.records
    assert (record.actor.type, record.actor.id) == (ActorType.AGENT, "provisioning_agent")
    assert (record.target, record.outcome) == ("tool:mocks.provision_laptop", AuditOutcome.SUCCESS)


def test_the_run_ends_completed_and_the_order_used_the_executors_stable_references(
    stack: Stack, tmp_path: Path
) -> None:
    async def body(p: Platform) -> str:
        run_id = await p.start(one_task_template("provisioning_agent", "provision_laptop"))
        await p.drive(run_id)
        assert await p.run_status(run_id) == "completed"
        return run_id

    run_id = on_platform(stack, tmp_path, body)
    created = stack.mocks_store.creations
    args = {
        "task_id": opaque_ref(ORG, "task", run_id, "t1"),
        "hire_id": opaque_ref(ORG, "hire", HIRE),
        "profile": "engineering",
        "office": "bangalore",
    }
    replay = call(
        stack, agent_claims("provisioning_agent", ORG, new_run(), PROVISIONING_SCOPES), "mocks__provision_laptop", args
    )
    assert replay.isError is False, text(replay)
    assert stack.mocks_store.creations == created  # the same key: the agent's order, replayed, nothing new


def test_a_bitbucket_account_task_completes_through_the_mocks(stack: Stack, tmp_path: Path) -> None:
    before = stack.mocks_store.creations
    run_id, task = _run_one(stack, tmp_path, "provisioning_agent", "create_bitbucket_account")
    assert task["status"] == "done", task.get("failure")
    assert stack.mocks_store.creations == before + 1
    [record] = trail(stack, run_id, ORG).records
    assert (record.target, record.outcome) == ("tool:mocks.create_bitbucket_account", AuditOutcome.SUCCESS)


def test_a_designer_gets_a_design_laptop(stack: Stack, tmp_path: Path) -> None:
    """The role the executor hands the agent decides the laptop profile: it reads the hire, not the template."""
    run_id, task = _run_one(stack, tmp_path, "provisioning_agent", "provision_laptop", role="designer")
    assert task["status"] == "done", task.get("failure")
    args = {
        "task_id": opaque_ref(ORG, "task", run_id, "t1"),
        "hire_id": opaque_ref(ORG, "hire", HIRE),
        "profile": "design",
        "office": "bangalore",
    }
    created = stack.mocks_store.creations
    replay = call(
        stack, agent_claims("provisioning_agent", ORG, new_run(), PROVISIONING_SCOPES), "mocks__provision_laptop", args
    )
    assert replay.isError is False
    assert stack.mocks_store.creations == created


# --- box 3: the seed template ----------------------------------------------------------------------


def _seed_template() -> Template:
    document = yaml.safe_load((ROOT / "templates" / "engineer-india.yaml").read_text(encoding="utf-8"))
    return Template.model_validate({**document, "version": 1, "status": "published"})


def test_the_seed_template_completes_every_non_approval_task_without_human_action(
    stack: Stack, tmp_path: Path, capsys: Any
) -> None:
    async def body(p: Platform) -> tuple[str, str, dict[str, dict[str, Any]]]:
        run_id = await p.start(_seed_template())
        await p.drive(run_id)
        return run_id, await p.run_status(run_id), await p.tasks(run_id)

    run_id, status, tasks = on_platform(stack, tmp_path, body)
    approval = {t for t, rec in tasks.items() if rec["type"] == "approval"}
    autonomous = {t: rec for t, rec in tasks.items() if t not in approval and rec["status"] != "skipped"}
    assert approval == {"manager_approval"}
    assert status == "completed"
    assert {t: rec["status"] for t, rec in autonomous.items()} == {t: "done" for t in autonomous}
    assert {t: rec["result"]["output"]["status"] for t, rec in autonomous.items()} == {t: "ok" for t in autonomous}
    assert len(autonomous) == 6
    with capsys.disabled():
        print(
            f"\nAGT-1 seed run {run_id}: {len(tasks)} tasks, {len(approval)} approval, "
            f"{len(autonomous)}/{len(autonomous)} non-approval tasks completed without human action"
        )
