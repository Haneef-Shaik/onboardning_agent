"""Agents and approvals (HIL-1 box 4; PRD FR-H1, FR-H5): the Approvals Agent prepares the packet for an
approval task, a `requires_approval` tool call the hub refused becomes an approval, and an agent that
answers `needs_human` waits for one instead of completing the task.

Every agent is AG-1's runtime on LLM-1's `fake` connector; the hub is a `ToolSource` double that refuses
the way the real hub does (`denied (<reason>): ...`). No real model is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from onb_agents.approvals import ApprovalDecider, ApprovalStore
from onb_contracts.approvals import APPROVAL_DECIDED
from onb_contracts.notify import Decision
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_orchestrator.consumer import resume_approval_handler

from .agent_support import MANIFESTS, ORG, Agents, Harness, Models, Tools, denying_tool, final, task_doc, tool_turn

PACKET = {
    "summary": "A MacBook Pro 14 and Bitbucket access, both standard for an engineer.",
    "recommendation": "approve",
    "risk_flags": [],
    "cited_policies": ["kb_access_policy"],
}
APPROVAL_TASK: dict[str, Any] = task_doc(
    "manager_approval",
    agent="approvals_agent",
    type="approval",
    action="approval",
    approver={"rule": "variable", "ref": "manager_id"},
    sla_hours=48,
)
DENIED = "denied (approval_required): approval required: create_bitbucket_account"


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def started(h: Harness, *tasks: dict[str, Any]) -> str:
    from .agent_support import HIRE, template

    async def go() -> str:
        with tenant_scope(ORG):  # the hire's manager is the default approver of a task without a rule
            await h.store.tenant("new_hires").update_one(
                {"_id": HIRE}, {"$set": {"manager_id": "u_test_manager"}}, upsert=True
            )
        return await h.start(template(*tasks))

    return run(go())


async def _approvals(h: Harness, run_id: str, task_id: str) -> list[dict[str, Any]]:
    with tenant_scope(ORG):
        return await ApprovalStore(h.store).for_task(run_id, task_id)


def approvals(h: Harness, run_id: str, task_id: str) -> list[dict[str, Any]]:
    return run(_approvals(h, run_id, task_id))


async def _decide_and_resume(h: Harness, run_id: str, approval_id: str, decision: Decision, executor: Any) -> None:
    with tenant_scope(ORG):
        decider = ApprovalDecider(ApprovalStore(h.store), h.bus)
        await decider.decide(approval_id, decision=decision, comment=None, channel="web", user_id="u_test_manager")
    payload = MappingProxyType({"approval_id": approval_id})
    job = JobContext("j", ORG, "orchestrator.resume_approval", "ev", APPROVAL_DECIDED, payload, run_id, 1)
    with tenant_scope(ORG):
        await resume_approval_handler(h.store, executor, bus=h.bus)(job)


# --- FR-H1: the Approvals Agent prepares the packet ----------------------------------------------------


def test_the_approvals_agent_prepares_the_packet_of_an_approval_task(tmp_path: Path) -> None:
    h = Harness.create(Models([final({"status": "ok", "packet": PACKET})], tmp_path))
    run_id = started(h, APPROVAL_TASK, task_doc("after", "manager_approval"))
    run(h.execute(run_id, h.executor()))
    [approval] = approvals(h, run_id, "manager_approval")
    assert approval["packet"] == PACKET
    assert approval["packet_failure"] is None
    assert run(h.task(run_id, "manager_approval"))["status"] == "waiting_approval"
    assert run(h.task(run_id, "after"))["status"] == "pending"
    [(agent_task, _)] = h.tools.for_task("manager_approval")
    assert agent_task.input["approval_id"] == approval["_id"]  # the agent reads the approval it summarises


def test_an_approval_task_whose_agent_fails_still_asks_the_human_and_says_why_there_is_no_packet(
    tmp_path: Path,
) -> None:
    h = Harness.create(Models([final({"nonsense": True})] * 2, tmp_path))  # invalid, and again after the repair retry
    run_id = started(h, APPROVAL_TASK)
    run(h.execute(run_id, h.executor()))
    [approval] = approvals(h, run_id, "manager_approval")
    assert approval["packet"] is None
    assert approval["packet_failure"] == "invalid_output"
    assert approval["status"] == "pending"
    assert run(h.task(run_id, "manager_approval"))["status"] == "waiting_approval"
    assert run(h.run(run_id))["status"] == "running"  # the run is not failed for a missing summary


def test_an_approval_task_whose_agent_needs_a_human_has_no_packet_either(tmp_path: Path) -> None:
    h = Harness.create(Models([final({"status": "needs_human", "explanation": "no policy covers this"})], tmp_path))
    run_id = started(h, APPROVAL_TASK)
    run(h.execute(run_id, h.executor()))
    [approval] = approvals(h, run_id, "manager_approval")
    assert (approval["packet"], approval["packet_failure"]) == (None, "needs_human")


def test_the_packet_is_never_replaced_by_a_later_run_of_the_agent(tmp_path: Path) -> None:
    h = Harness.create(Models([final({"status": "ok", "packet": PACKET})], tmp_path))
    run_id = started(h, APPROVAL_TASK)
    run(h.execute(run_id, h.executor()))
    [approval] = approvals(h, run_id, "manager_approval")

    async def again() -> None:
        with tenant_scope(ORG):
            await ApprovalStore(h.store).set_packet(approval["_id"], {**PACKET, "recommendation": "reject"})

    run(again())
    [after] = approvals(h, run_id, "manager_approval")
    assert after["packet"]["recommendation"] == "approve"


# --- needs_human -----------------------------------------------------------------------------------------


def test_an_agent_that_answers_needs_human_waits_for_an_approval_instead_of_completing(tmp_path: Path) -> None:
    h = Harness.create(Models([final({"status": "needs_human", "answer": {"reason": "unsure"}})], tmp_path))
    run_id = started(h, task_doc("ask"))
    run(h.execute(run_id, h.executor()))
    assert run(h.task(run_id, "ask"))["status"] == "waiting_approval"
    [approval] = approvals(h, run_id, "ask")
    assert (approval["kind"], approval["rule"], approval["status"]) == ("needs_human", None, "pending")
    assert run(h.run(run_id))["status"] == "running"


# --- box 4: a requires_approval tool call creates an approval task ---------------------------------------


def _bitbucket_script() -> list[dict[str, Any]]:
    done = {"status": "ok", "provisioned": [{"action": "create_bitbucket_account", "done": True}]}
    return [tool_turn("mocks__create_bitbucket_account", {"username": "new.hire"}), final(done)]


def test_a_requires_approval_tool_call_creates_an_approval_task(tmp_path: Path) -> None:
    """Box 4: the hub refuses the call under the manifest's rule; the task becomes an approval."""
    agents = Agents(requires_approval={"provisioning_agent": ("create_bitbucket_account",)})
    h = Harness.create(
        Models(_bitbucket_script(), tmp_path),
        Tools(bound=(denying_tool("mocks__create_bitbucket_account", DENIED),)),
        agents,
    )
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    run(h.execute(run_id, h.executor()))
    [approval] = approvals(h, run_id, "bitbucket")
    assert approval["kind"] == "tool_call"
    assert approval["rule"] == "create_bitbucket_account"
    assert approval["status"] == "pending"
    assert approval["approver_id"] == "u_test_manager"
    task = run(h.task(run_id, "bitbucket"))
    assert task["status"] == "waiting_approval"
    assert task["approval_id"] == approval["_id"]
    assert run(h.run(run_id))["status"] == "running"
    requested = [e for e in h.bus.published if e["type"] == "approval.requested"]
    assert [e["payload"]["approval_id"] for e in requested] == [approval["_id"]]


def test_an_approved_tool_call_runs_the_task_again_with_that_rule_in_the_agents_token(tmp_path: Path) -> None:
    agents = Agents(requires_approval={"provisioning_agent": ("create_bitbucket_account",)})
    h = Harness.create(
        Models(_bitbucket_script(), tmp_path),
        Tools(bound=(denying_tool("mocks__create_bitbucket_account", DENIED),)),
        agents,
    )
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    executor = h.executor()
    run(h.execute(run_id, executor))
    [approval] = approvals(h, run_id, "bitbucket")
    run(_decide_and_resume(h, run_id, approval["_id"], Decision.APPROVE, executor))
    first, second = (t for t, _ in h.tools.for_task("bitbucket"))
    assert first.approvals == ()  # nothing approved the first time
    assert second.approvals == ("create_bitbucket_account",)  # the human's approval, from the stored record
    from onb_agents.runtime import task_scopes

    assert "approved:create_bitbucket_account" in task_scopes(MANIFESTS["provisioning_agent"].model_copy(
        update={"requires_approval": ("create_bitbucket_account",)}), second.approvals)  # fmt: skip


def test_a_rejected_tool_call_never_gets_its_rule_approved(tmp_path: Path) -> None:
    agents = Agents(requires_approval={"provisioning_agent": ("create_bitbucket_account",)})
    h = Harness.create(
        Models(_bitbucket_script(), tmp_path),
        Tools(bound=(denying_tool("mocks__create_bitbucket_account", DENIED),)),
        agents,
    )
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    executor = h.executor()
    run(h.execute(run_id, executor))
    [approval] = approvals(h, run_id, "bitbucket")
    run(_decide_and_resume(h, run_id, approval["_id"], Decision.REJECT, executor))
    assert len(h.tools.for_task("bitbucket")) == 1  # the agent did not run again
    assert run(h.task(run_id, "bitbucket"))["status"] == "failed"
    assert run(h.run(run_id))["status"] == "failed"


def test_a_call_the_hub_denies_for_another_reason_is_not_an_approval(tmp_path: Path) -> None:
    agents = Agents(requires_approval={"provisioning_agent": ("create_bitbucket_account",)})
    h = Harness.create(
        Models(_bitbucket_script(), tmp_path),
        Tools(bound=(denying_tool("mocks__create_bitbucket_account", "denied (tool_not_in_manifest): no"),)),
        agents,
    )
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    run(h.execute(run_id, h.executor()))
    assert approvals(h, run_id, "bitbucket") == []
    assert run(h.task(run_id, "bitbucket"))["status"] == "done"


def test_an_approval_denial_for_a_tool_the_manifest_has_no_rule_for_is_not_an_approval(tmp_path: Path) -> None:
    """The hub's reason code alone is not enough: the rule must be one the manifest lists, so an agent cannot
    open approvals by naming tools."""
    h = Harness.create(
        Models(_bitbucket_script(), tmp_path), Tools(bound=(denying_tool("mocks__create_bitbucket_account", DENIED),))
    )  # manifest: no rules
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    run(h.execute(run_id, h.executor()))
    assert approvals(h, run_id, "bitbucket") == []
    assert run(h.task(run_id, "bitbucket"))["status"] == "done"


def test_an_agent_cannot_widen_its_own_scopes_from_a_stored_approval_of_another_task(tmp_path: Path) -> None:
    """Approvals are per task: another task's approved rule never reaches this task's token."""
    agents = Agents(requires_approval={"provisioning_agent": ("create_bitbucket_account",)})
    h = Harness.create(Models([final({"status": "ok", "provisioned": []})], tmp_path), Tools(), agents)
    run_id = started(h, task_doc("one", agent="provisioning_agent"), task_doc("two", "one", agent="provisioning_agent"))

    async def seed() -> None:
        from onb_agents.approvals import ApprovalSpec

        with tenant_scope(ORG):
            store = ApprovalStore(h.store)
            made = await store.create(
                ApprovalSpec(
                    run_id, "one", "h_1", "tool_call", "create_bitbucket_account", "u_test_manager", None, "x", [], None
                ),
                now=datetime(2026, 11, 12, tzinfo=UTC),
            )
            await ApprovalDecider(store, h.bus).decide(
                made["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id="u_test_manager"
            )

    run(seed())
    run(h.execute(run_id, h.executor()))
    assert [t.approvals for t, _ in h.tools.for_task("two")] == [()]
    assert [t.approvals for t, _ in h.tools.for_task("one")] == [("create_bitbucket_account",)]


def test_a_refused_call_that_matches_none_of_the_manifests_rules_is_not_an_approval(tmp_path: Path) -> None:
    """The manifest lists a rule for another tool: the hub's `approval_required` for this tool opens nothing."""
    agents = Agents(requires_approval={"provisioning_agent": ("provision_laptop",)})
    tool = denying_tool("mocks__create_bitbucket_account", DENIED)
    h = Harness.create(Models(_bitbucket_script(), tmp_path), Tools(bound=(tool,)), agents)
    run_id = started(h, task_doc("bitbucket", agent="provisioning_agent", action="create_bitbucket_account"))
    run(h.execute(run_id, h.executor()))
    assert approvals(h, run_id, "bitbucket") == []
    assert run(h.task(run_id, "bitbucket"))["status"] == "done"
