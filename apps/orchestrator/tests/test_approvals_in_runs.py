"""Approval tasks pause their branch and resume on the first decision (HIL-1; PRD FR-H1-H3, FR-H5).

The run is started with the real `RunStarter` and executed by the real `orchestrator.start_run` handler
over the in-memory driver; decisions go through the real `ApprovalDecider`, and `orchestrator.resume_approval`
resumes the checkpointed graph. The real bus, transactions and replica set are in `tests_int/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.approvals import ApprovalDecider, ApprovalStore
from onb_agents.runs import RUN_STARTED, START_RUN, RunStarter, RunStore
from onb_agents.runs.testing import OutboxStandIn, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.approvals import APPROVAL_DECIDED, APPROVAL_REQUESTED
from onb_contracts.notify import Decision
from onb_contracts.templates import Template
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext
from onb_orchestrator.checkpointer import CHECKPOINT_WRITES, CHECKPOINTS
from onb_orchestrator.consumer import resume_approval_handler, start_run_handler
from onb_orchestrator.executor import ApprovalRequestedError, StubExecutor, TaskWork
from onb_orchestrator.runner import TaskStepError

ORG = "org_a"
MANAGER = "u_test_manager"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id.upper()}",
        "agent": "provisioning_agent",
        "action": f"do_{task_id}",
        "depends_on": list(depends_on),
        **extra,
    }


def _approval_task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
    return _task(
        task_id,
        *depends_on,
        type="approval",
        agent="approvals_agent",
        action="approval",
        approver={"rule": "variable", "ref": "manager_id"},
        sla_hours=48,
        **extra,
    )


def _template(tasks: list[dict[str, Any]]) -> Template:
    return Template.model_validate(
        {
            "key": "approval-test",
            "name": "Approval (test)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": tasks,
        }
    )


def _one_approval() -> Template:
    return _template([_task("a"), _approval_task("appr", "a"), _task("b", "appr"), _task("c", "appr")])


@dataclass
class Executor(StubExecutor):
    """The stub, plus tasks that ask for an approval (`requests`) the first time they run."""

    requests: dict[str, list[ApprovalRequestedError]] = field(default_factory=dict)

    async def execute(self, work: TaskWork) -> None:
        await super().execute(work)
        pending = self.requests.get(work.task_id)
        if pending:
            raise pending.pop(0)


@dataclass
class World:
    store: DataStore
    bus: OutboxStandIn
    executor: Executor
    runs: RunStore
    approvals: ApprovalStore
    decider: ApprovalDecider

    async def start(self, template: Template | None = None, *, manager: str | None = MANAGER) -> str:
        db_hire = {"_id": "h_1", "manager_id": manager} if manager else {"_id": "h_1"}
        with tenant_scope(ORG):
            await self.store.tenant("new_hires").insert_one(db_hire)
            started = await RunStarter(self.runs, self.bus, InMemoryAuditLog()).start(
                template=template or _one_approval(),
                hire_id="h_1",
                variables=hire_variables_for(),
                actor_id="u_hr",
            )
        run_id = str(started.run["_id"])
        await self._deliver(START_RUN, RUN_STARTED, {"run_id": run_id}, run_id)
        return run_id

    async def _deliver(self, consumer: str, event: str, payload: dict[str, Any], run_id: str) -> None:
        job = JobContext("job", ORG, consumer, "ev", event, MappingProxyType(payload), run_id, 1)
        handler = (
            start_run_handler(self.store, self.executor, bus=self.bus)
            if consumer == START_RUN
            else resume_approval_handler(self.store, self.executor, bus=self.bus)
        )
        with tenant_scope(ORG):
            await handler(job)

    async def decide(
        self,
        approval_id: str,
        decision: Decision = Decision.APPROVE,
        *,
        user: str = MANAGER,
        comment: str | None = None,
    ) -> None:
        with tenant_scope(ORG):
            await self.decider.decide(approval_id, decision=decision, comment=comment, channel="web", user_id=user)

    async def resume(self, approval_id: str, run_id: str) -> None:
        await self._deliver("orchestrator.resume_approval", APPROVAL_DECIDED, {"approval_id": approval_id}, run_id)

    async def tasks(self, run_id: str) -> dict[str, dict[str, Any]]:
        with tenant_scope(ORG):
            return {t["task_id"]: t for t in await self.runs.tasks(run_id)}

    async def status(self, run_id: str) -> str:
        with tenant_scope(ORG):
            found = await self.runs.get(run_id)
        assert found is not None
        return str(found["status"])

    async def approval_of(self, run_id: str, task_id: str) -> dict[str, Any]:
        with tenant_scope(ORG):
            [found] = await self.approvals.for_task(run_id, task_id)
        return found

    def events(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.bus.published if e["type"] == event_type]


def _world(executor: Executor | None = None) -> World:
    store = memory_store(MemoryDatabase())
    bus = OutboxStandIn()
    approvals = ApprovalStore(store)
    return World(
        store,
        bus,
        executor or Executor(),
        RunStore(store),
        approvals,
        ApprovalDecider(approvals, bus, clock=lambda: NOW),
    )


# --- FR-H1: an approval task pauses its branch ---------------------------------------------------------


def test_an_approval_task_pauses_the_run_and_nothing_after_it_runs() -> None:
    world = _world()

    async def go() -> tuple[dict[str, dict[str, Any]], str]:
        run_id = await world.start()
        return await world.tasks(run_id), await world.status(run_id)

    tasks, status = run(go())
    assert tasks["a"]["status"] == "done"
    assert tasks["appr"]["status"] == "waiting_approval"
    assert tasks["b"]["status"] == tasks["c"]["status"] == "pending"
    assert status == "running"  # not completed, not failed: the run waits
    assert world.executor.calls == ["a", "appr"]  # the Approvals Agent ran (packet); b and c did not


def test_the_approval_is_opened_for_the_approver_with_what_it_asks() -> None:
    world = _world()

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        run_id = await world.start()
        return await world.approval_of(run_id, "appr"), (await world.tasks(run_id))["appr"]

    approval, task = run(go())
    assert approval["kind"] == "task"
    assert approval["status"] == "pending"
    assert approval["approver_id"] == MANAGER
    assert approval["hire_id"] == "h_1"
    assert approval["request"] == {"items": ["Task B", "Task C"]}  # what approving lets run
    assert approval["sla_hours"] == 48
    assert task["approval_id"] == approval["_id"]


def test_one_approval_requested_event_is_published_with_the_pause() -> None:
    world = _world()

    async def go() -> str:
        return await world.start()

    run_id = run(go())
    [event] = world.events(APPROVAL_REQUESTED)
    assert event["payload"].keys() == {"approval_id", "run_id", "task_id"}
    assert event["payload"]["run_id"] == run_id
    assert event["payload"]["task_id"] == "appr"


def test_the_start_job_delivered_again_while_the_run_waits_changes_nothing() -> None:
    world = _world()

    async def go() -> tuple[list[str], int]:
        run_id = await world.start()
        await world._deliver(START_RUN, RUN_STARTED, {"run_id": run_id}, run_id)
        return list(world.executor.calls), len(world.events(APPROVAL_REQUESTED))

    calls, requested = run(go())
    assert calls == ["a", "appr"]
    assert requested == 1


def test_without_a_manager_the_approval_goes_to_the_hr_role() -> None:
    world = _world()

    async def go() -> dict[str, Any]:
        run_id = await world.start(manager=None)
        return await world.approval_of(run_id, "appr")

    approval = run(go())
    assert approval["approver_id"] is None
    assert approval["approver_role"] == "HR"


# --- FR-H2/H3: the decision resumes the run -------------------------------------------------------------


def test_an_approval_resumes_the_run_and_completes_it() -> None:
    world = _world()

    async def go() -> tuple[dict[str, dict[str, Any]], str]:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.decide(approval["_id"], comment="Fine")
        await world.resume(approval["_id"], run_id)
        return await world.tasks(run_id), await world.status(run_id)

    tasks, status = run(go())
    assert {t["status"] for t in tasks.values()} == {"done"}
    assert status == "completed"
    assert world.executor.calls.count("appr") == 1  # the approval task did not run again
    assert tasks["b"]["started_at"] >= tasks["appr"]["finished_at"]


def test_a_rejection_fails_the_task_cancels_what_follows_and_fails_the_run() -> None:
    world = _world()

    async def go() -> tuple[dict[str, dict[str, Any]], str]:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.decide(approval["_id"], Decision.REJECT, comment="No")
        await world.resume(approval["_id"], run_id)
        return await world.tasks(run_id), await world.status(run_id)

    tasks, status = run(go())
    assert tasks["appr"]["status"] == "failed"
    assert tasks["appr"]["failure"] == {"kind": "approval_rejected", "reason": "reject"}
    assert tasks["b"]["status"] == tasks["c"]["status"] == "cancelled"
    assert status == "failed"
    assert "b" not in world.executor.calls


def test_the_resume_job_delivered_twice_runs_nothing_twice() -> None:
    world = _world()

    async def go() -> str:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)
        await world.resume(approval["_id"], run_id)
        return await world.status(run_id)

    assert run(go()) == "completed"
    assert sorted(world.executor.calls) == ["a", "appr", "b", "c"]


def test_a_resume_for_an_approval_nobody_decided_does_nothing() -> None:
    world = _world()

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.resume(approval["_id"], run_id)
        return await world.status(run_id), {k: v["status"] for k, v in (await world.tasks(run_id)).items()}

    status, tasks = run(go())
    assert status == "running"
    assert tasks["appr"] == "waiting_approval"


def test_a_resume_that_cannot_find_the_paused_graph_is_retried_not_dropped() -> None:
    """The decision can land before the start job has checkpointed its pause: retry, never lose it."""
    world = _world()

    async def go() -> None:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.decide(approval["_id"])
        with tenant_scope(ORG):  # drop the checkpoint the pause wrote
            for name in (CHECKPOINTS, CHECKPOINT_WRITES):
                await world.store.tenant(name).delete_many({})
        await world.resume(approval["_id"], run_id)

    with pytest.raises(TaskStepError) as raised:
        run(go())
    assert raised.value.transient


def test_a_resume_for_another_orgs_approval_does_nothing() -> None:
    world = _world()

    async def go() -> list[str]:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        await world.decide(approval["_id"])
        job = JobContext(
            "job", "org_b", "orchestrator.resume_approval", "ev", APPROVAL_DECIDED,
            MappingProxyType({"approval_id": approval["_id"]}), run_id, 1,
        )  # fmt: skip
        with tenant_scope("org_b"):
            await resume_approval_handler(world.store, world.executor, bus=world.bus)(job)
        return [t["status"] for t in (await world.tasks(run_id)).values()]

    assert "waiting_approval" in run(go())


# --- parallel approvals -----------------------------------------------------------------------------------


def test_two_approvals_in_parallel_branches_each_resume_only_their_own_task() -> None:
    template = _template([_task("a"), _approval_task("p1", "a"), _approval_task("p2", "a"), _task("z", "p1", "p2")])
    world = _world()

    async def go() -> list[tuple[str, dict[str, str]]]:
        run_id = await world.start(template)
        seen: list[tuple[str, dict[str, str]]] = []
        for task in ("p1", "p2"):
            approval = await world.approval_of(run_id, task)
            await world.decide(approval["_id"])
            await world.resume(approval["_id"], run_id)
            seen.append((await world.status(run_id), {k: v["status"] for k, v in (await world.tasks(run_id)).items()}))
        return seen

    (status1, tasks1), (status2, tasks2) = run(go())
    assert status1 == "running"
    assert tasks1["p1"] == "done"
    assert tasks1["p2"] == "waiting_approval"
    assert tasks1["z"] == "pending"
    assert status2 == "completed"
    assert tasks2["z"] == "done"


# --- needs_human and requires_approval tool calls (FR-H5) -------------------------------------------------


def test_an_agent_that_needs_a_human_pauses_its_task_and_the_approval_completes_it() -> None:
    template = _template([_task("a"), _task("b", "a")])
    world = _world(Executor(requests={"a": [ApprovalRequestedError("needs_human")]}))

    async def go() -> tuple[str, str, dict[str, Any]]:
        run_id = await world.start(template)
        waiting = (await world.tasks(run_id))["a"]["status"]
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)
        return waiting, await world.status(run_id), approval

    waiting, status, approval = run(go())
    assert waiting == "waiting_approval"
    assert approval["kind"] == "needs_human"
    assert approval["approver_id"] == MANAGER  # no approver rule on the task: the hire's manager
    assert status == "completed"
    assert world.executor.calls == ["a", "b"]  # the agent is not run again: a human said go on


def test_a_requires_approval_tool_call_opens_an_approval_and_an_approve_runs_the_task_again() -> None:
    """Box 4 (FR-H5): the call the hub refused becomes an approval; approving lets the task run again."""
    template = _template([_task("a"), _task("b", "a")])
    world = _world(Executor(requests={"a": [ApprovalRequestedError("tool_call", "create_bitbucket_account")]}))

    async def go() -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
        run_id = await world.start(template)
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)
        return approval, await world.tasks(run_id), await world.status(run_id)

    approval, tasks, status = run(go())
    assert approval["kind"] == "tool_call"
    assert approval["rule"] == "create_bitbucket_account"
    assert approval["status"] == "pending"  # as it was opened
    assert world.executor.calls == ["a", "a", "b"]  # the task ran again, then its dependent
    assert tasks["a"]["attempts"] == 1  # the approval's re-run is not a retry of the task
    assert status == "completed"
    assert len(world.events(APPROVAL_REQUESTED)) == 1


def test_a_rejected_tool_call_approval_fails_the_task_without_running_it_again() -> None:
    template = _template([_task("a"), _task("b", "a")])
    world = _world(Executor(requests={"a": [ApprovalRequestedError("tool_call", "create_bitbucket_account")]}))

    async def go() -> tuple[dict[str, dict[str, Any]], str]:
        run_id = await world.start(template)
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"], Decision.REJECT)
        await world.resume(approval["_id"], run_id)
        return await world.tasks(run_id), await world.status(run_id)

    tasks, status = run(go())
    assert tasks["a"]["status"] == "failed"
    assert tasks["a"]["failure"]["kind"] == "approval_rejected"
    assert status == "failed"
    assert world.executor.calls == ["a"]


def test_a_second_rule_the_re_run_needs_opens_a_second_approval() -> None:
    template = _template([_task("a")])
    first = ApprovalRequestedError("tool_call", "create_bitbucket_account")
    second = ApprovalRequestedError("tool_call", "update_one:documents_meta")
    requests = {"a": [first, second]}
    world = _world(Executor(requests=requests))

    async def go() -> tuple[list[str], str]:
        run_id = await world.start(template)
        for _ in range(2):
            with tenant_scope(ORG):
                pending = [x for x in await world.approvals.for_task(run_id, "a") if x["status"] == "pending"]
            [approval] = pending
            await world.decide(approval["_id"])
            await world.resume(approval["_id"], run_id)
        with tenant_scope(ORG):
            rules = [x["rule"] for x in await world.approvals.for_task(run_id, "a")]
        return rules, await world.status(run_id)

    rules, status = run(go())
    assert rules == ["create_bitbucket_account", "update_one:documents_meta"]
    assert status == "completed"
    assert world.executor.calls == ["a", "a", "a"]


def test_a_tool_call_the_hub_still_refuses_after_its_approval_fails_the_task() -> None:
    """An approval that did not unlock the call must not loop: the same rule asked again fails the task."""
    template = _template([_task("a")])
    again = ApprovalRequestedError("tool_call", "create_bitbucket_account")
    world = _world(Executor(requests={"a": [again, ApprovalRequestedError("tool_call", "create_bitbucket_account")]}))

    async def go() -> tuple[dict[str, Any], str]:
        run_id = await world.start(template)
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)
        return (await world.tasks(run_id))["a"], await world.status(run_id)

    task, status = run(go())
    assert task["status"] == "failed"
    assert task["failure"]["kind"] == "approval_ineffective"
    assert status == "failed"


def test_a_third_approval_for_one_task_fails_it_visibly_instead_of_wedging_the_run() -> None:
    """LangGraph replays a node's earlier resume values, so a chain of more than two approvals in one node
    would stall (review finding): the limit is two per task, and the third request fails the task."""
    template = _template([_task("a")])
    asks = [ApprovalRequestedError("tool_call", f"rule_{n}") for n in range(3)]
    world = _world(Executor(requests={"a": asks}))

    async def go() -> tuple[dict[str, Any], str]:
        run_id = await world.start(template)
        for _ in range(2):
            with tenant_scope(ORG):
                [approval] = [x for x in await world.approvals.for_task(run_id, "a") if x["status"] == "pending"]
            await world.decide(approval["_id"])
            await world.resume(approval["_id"], run_id)
        return (await world.tasks(run_id))["a"], await world.status(run_id)

    task, status = run(go())
    assert task["status"] == "failed"
    assert task["failure"]["kind"] == "approval_limit"
    assert status == "failed"


def test_a_failed_run_cancels_the_approvals_still_pending() -> None:
    template = _template([_task("a"), _approval_task("p1", "a"), _approval_task("p2", "a")])
    world = _world()

    async def go() -> list[str]:
        run_id = await world.start(template)
        first = await world.approval_of(run_id, "p1")
        await world.decide(first["_id"], Decision.REJECT)
        await world.resume(first["_id"], run_id)
        with tenant_scope(ORG):
            return [x["status"] for x in await world.approvals.for_task(run_id, "p2")]

    assert run(go()) == ["cancelled"]


def test_a_resume_that_finds_the_approved_task_running_again_is_retried_not_dropped() -> None:
    """A transient failure after an approved tool call started its re-run leaves the task `running`: the
    resume job must fail visibly (retry, then dead-letter), never be acknowledged as done."""
    template = _template([_task("a")])
    world = _world(Executor(requests={"a": [ApprovalRequestedError("tool_call", "r")]}))

    async def go() -> None:
        run_id = await world.start(template)
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"])
        with tenant_scope(ORG):
            await world.store.tenant("run_tasks").update_one(
                {"run_id": run_id, "task_id": "a"}, {"$set": {"status": "running"}}
            )
        await world.resume(approval["_id"], run_id)

    with pytest.raises(TaskStepError) as raised:
        run(go())
    assert raised.value.transient


def test_settling_a_task_whose_approval_is_still_pending_is_not_settled() -> None:
    """The guard under the resume handler's own check: a graph resumed too early must not finish the task."""
    from onb_orchestrator.runner import RunExecutor, TaskNotSettledError

    world = _world()

    async def go() -> None:
        run_id = await world.start()
        with tenant_scope(ORG):
            await RunExecutor(world.store, world.executor, bus=world.bus).settle(run_id, "appr", traceparent=None)

    with pytest.raises(TaskNotSettledError):
        run(go())


# --- RUN-2 control (pause, cancel) and approvals -------------------------------------------------------------


def test_an_approval_decided_while_the_run_is_paused_is_settled_when_hr_resumes_the_run() -> None:
    """The decision is stored, the resume job finds the run paused and leaves it, and driving the run on later
    settles the task from the stored decision instead of pausing it again."""
    from onb_agents.runs import RunState

    world = _world()

    async def go() -> tuple[str, dict[str, str]]:
        run_id = await world.start()
        approval = await world.approval_of(run_id, "appr")
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.PAUSED, at=NOW, by="u_hr")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)  # paused: nothing happens
        paused = {k: v["status"] for k, v in (await world.tasks(run_id)).items()}
        assert paused["appr"] == "waiting_approval"
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.RUNNING, at=NOW, by="u_hr")
        await world._deliver(START_RUN, "run.resumed", {"run_id": run_id}, run_id)
        return await world.status(run_id), {k: v["status"] for k, v in (await world.tasks(run_id)).items()}

    status, tasks = run(go())
    assert status == "completed"
    assert set(tasks.values()) == {"done"}


def test_a_cancelled_run_cancels_its_pending_approvals() -> None:
    from onb_agents.runs import RunState
    from onb_orchestrator.consumer import cleanup_run_handler

    world = _world()

    async def go() -> list[str]:
        run_id = await world.start()
        with tenant_scope(ORG):
            await world.runs.move_run(run_id, RunState.CANCELLED, at=NOW, by="u_hr")
        payload = MappingProxyType({"run_id": run_id})
        job = JobContext("j", ORG, "orchestrator.cleanup_run", "ev", "run.cancelled", payload, run_id, 1)
        with tenant_scope(ORG):
            await cleanup_run_handler(world.store)(job)
            return [a["status"] for a in await world.approvals.for_task(run_id, "appr")]

    assert run(go()) == ["cancelled"]


def test_an_approved_tool_calls_re_run_holds_a_lease_like_any_running_task() -> None:
    """RUN-2: a `running` task without a live lease is taken over by the next delivery. The approved re-run
    must hold its own lease while the agent works, and be released when it is done."""
    template = _template([_task("a")])
    seen: list[dict[str, Any]] = []

    class Spy(Executor):
        async def execute(self, work: TaskWork) -> None:
            with tenant_scope(ORG):
                seen.append(dict(await world.runs.task(work.run_id, work.task_id) or {}))
            await super().execute(work)

    world = _world(Spy(requests={"a": [ApprovalRequestedError("tool_call", "r")]}))

    async def go() -> dict[str, Any]:
        run_id = await world.start(template)
        approval = await world.approval_of(run_id, "a")
        await world.decide(approval["_id"])
        await world.resume(approval["_id"], run_id)
        return (await world.tasks(run_id))["a"]

    done = run(go())
    first, rerun = seen
    assert first["status"] == rerun["status"] == "running"
    assert first["lease_token"]
    assert rerun["lease_token"]
    assert rerun["lease_token"] != first["lease_token"]  # a new holder: the first lease was released when it parked
    assert done["status"] == "done"
    assert done["lease_token"] is None  # released with the finish
