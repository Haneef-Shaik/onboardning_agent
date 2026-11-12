"""The approval SLA sweep (HIL-2, FR-H4): an overdue approval reminds its approver once, then escalates to HR.

A fake clock drives every test (no real waiting). The store is the real `DataStore` over the in-memory driver,
the bus the outbox stand-in; the real transaction (stamp and event commit together) is proved in
`tests_int/test_approval_sla_against_mongo.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from onb_agents.approvals import ApprovalSpec, ApprovalStore
from onb_agents.approvals.sla import SlaPolicy, load_sla_policy
from onb_agents.runs.testing import OutboxStandIn
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.notify import APPROVAL_OVERDUE
from onb_data import tenant_scope
from onb_orchestrator.approval_sla import ApprovalSlaJob, SlaReport, run_sla_forever

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
POLICY = SlaPolicy(default_sla_hours=24, remind_after_sla=1.0, escalate_after_sla=2.0, escalate_to_role="HR")


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def at(self, hours: float) -> None:
        self.now = T0 + timedelta(hours=hours)


def _spec(task_id: str = "manager_approval", *, sla_hours: int | None = 10, **changes: Any) -> ApprovalSpec:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "task_id": task_id,
        "hire_id": "h_1",
        "kind": "task",
        "rule": None,
        "approver_id": "u_mgr",
        "approver_role": None,
        "title": "Manager approves equipment",
        "items": ["Laptop"],
        "sla_hours": sla_hours,
    }
    return ApprovalSpec(**{**base, **changes})


class World:
    """One database; `job()` is a worker process over it (a new one per restart)."""

    def __init__(self, *orgs: str) -> None:
        self.db = MemoryDatabase()
        self.bus = OutboxStandIn()
        self.clock = Clock(T0)
        store = memory_store(self.db)
        for org in orgs or ("org_a",):
            run(store.shared("orgs").insert_one({"_id": org, "name": org}))

    def job(self, policy: SlaPolicy = POLICY) -> ApprovalSlaJob:
        return ApprovalSlaJob(memory_store(self.db), self.bus, policy, clock=self.clock)

    def open(self, spec: ApprovalSpec, *, org: str = "org_a", hours: float = 0.0) -> str:
        async def go() -> str:
            with tenant_scope(org):
                created = await ApprovalStore(memory_store(self.db)).create(spec, now=T0 + timedelta(hours=hours))
                return str(created["_id"])

        return run(go())

    def approval(self, approval_id: str, *, org: str = "org_a") -> dict[str, Any]:
        async def go() -> dict[str, Any]:
            with tenant_scope(org):
                found = await ApprovalStore(memory_store(self.db)).get(approval_id)
                assert found is not None
                return found

        return run(go())

    def stages(self) -> list[tuple[str, str, str]]:
        return [
            (p["org_id"], p["payload"]["approval_id"], p["payload"]["stage"])
            for p in self.bus.published
            if p["type"] == APPROVAL_OVERDUE
        ]


# --- box 1: one reminder, then an escalation at the second threshold ------------------------------------


def test_an_overdue_approval_reminds_once_then_escalates_to_hr_at_the_second_threshold() -> None:
    world = World()
    approval_id = world.open(_spec(sla_hours=10))
    job = world.job()

    for hours in (0, 5, 9.99):  # inside the SLA: nothing
        world.clock.at(hours)
        assert run(job.run_once()) == SlaReport()
    assert world.stages() == []

    world.clock.at(10)  # overdue: the approver is reminded
    assert run(job.run_once()) == SlaReport(reminders=1)
    for hours in (10, 12, 15, 19.99):  # still overdue, below the second threshold: no second reminder
        world.clock.at(hours)
        assert run(job.run_once()) == SlaReport()
    assert world.stages() == [("org_a", approval_id, "reminder")]

    world.clock.at(20)  # the second threshold: escalated to HR
    assert run(job.run_once()) == SlaReport(escalations=1)
    for hours in (21, 48, 24 * 30):  # and nothing after that, however long it waits
        world.clock.at(hours)
        assert run(job.run_once()) == SlaReport()

    assert world.stages() == [("org_a", approval_id, "reminder"), ("org_a", approval_id, "escalation")]
    reminder, escalation = (p for p in world.bus.published if p["type"] == APPROVAL_OVERDUE)
    assert reminder["payload"] == {
        "approval_id": approval_id,
        "run_id": "run_1",
        "task_id": "manager_approval",
        "stage": "reminder",
        "escalate_to": None,
    }
    assert escalation["payload"]["escalate_to"] == "HR"
    assert (reminder["idempotency_key"], escalation["idempotency_key"]) == (
        f"approval:{approval_id}:reminder",
        f"approval:{approval_id}:escalation",
    )
    assert reminder["run_id"] == "run_1"
    stored = world.approval(approval_id)
    assert stored["reminded_at"] == T0 + timedelta(hours=10)
    assert stored["escalated_at"] == T0 + timedelta(hours=20)
    assert stored["status"] == "pending"  # escalation tells HR; it does not decide


def test_an_approval_first_seen_past_both_thresholds_is_reminded_then_escalated_in_order() -> None:
    world = World()
    approval_id = world.open(_spec(sla_hours=2))
    world.clock.at(50)
    assert run(world.job().run_once()) == SlaReport(reminders=1, escalations=1)
    assert world.stages() == [("org_a", approval_id, "reminder"), ("org_a", approval_id, "escalation")]


def test_a_decided_or_cancelled_approval_is_never_reminded() -> None:
    world = World()
    world.open(_spec("approved_task"))
    world.open(_spec("cancelled_task"))

    async def settle() -> None:
        with tenant_scope("org_a"):
            approvals = ApprovalStore(memory_store(world.db))
            decided = _spec("approved_task").approval_id
            await approvals.decide_if_pending(decided, {"decision": "approve"}, "approved")
            await approvals.cancel_pending("run_1", at=T0)

    run(settle())
    world.clock.at(100)
    assert run(world.job().run_once()) == SlaReport()
    assert world.stages() == []


def test_a_decision_that_lands_during_the_pass_wins_and_nothing_is_sent() -> None:
    world = World()
    approval_id = world.open(_spec())
    world.clock.at(30)
    job = world.job()

    async def decide_then_sweep() -> SlaReport:
        # the sweep read the approval as pending; the approver decides before the stamp is written
        original = ApprovalStore.mark_stage

        async def decided_first(self: ApprovalStore, *args: Any, **kwargs: Any) -> bool:
            await self.decide_if_pending(approval_id, {"decision": "approve"}, "approved")
            return await original(self, *args, **kwargs)

        ApprovalStore.mark_stage = decided_first  # type: ignore[method-assign]
        try:
            return await job.run_once()
        finally:
            ApprovalStore.mark_stage = original  # type: ignore[method-assign]

    assert run(decide_then_sweep()) == SlaReport()
    assert world.stages() == []
    assert world.approval(approval_id).get("reminded_at") is None


def test_an_approval_without_an_sla_takes_the_configured_default() -> None:
    world = World()
    tool_call = world.open(_spec("t_tool", sla_hours=None, kind="tool_call", rule="hris.write"))
    world.clock.at(23)
    assert run(world.job().run_once()) == SlaReport()
    world.clock.at(24)
    assert run(world.job().run_once()) == SlaReport(reminders=1)
    assert world.stages() == [("org_a", tool_call, "reminder")]


def test_each_org_is_swept_in_its_own_scope() -> None:
    world = World("org_a", "org_b")
    in_a = world.open(_spec(), org="org_a")
    in_b = world.open(_spec(run_id="run_2"), org="org_b")
    world.clock.at(10)
    assert run(world.job().run_once()) == SlaReport(reminders=2)
    assert sorted(world.stages()) == [("org_a", in_a, "reminder"), ("org_b", in_b, "reminder")]
    assert world.approval(in_a, org="org_a")["reminded_at"] is not None
    assert world.approval(in_b, org="org_b")["reminded_at"] is not None


def test_one_orgs_failure_does_not_stop_the_others() -> None:
    world = World("org_a", "org_b")
    world.open(_spec(), org="org_a")
    in_b = world.open(_spec(run_id="run_2"), org="org_b")
    world.clock.at(10)

    class FailingInA(OutboxStandIn):
        async def publish(self, event_type: str, payload: Any, **kwargs: Any) -> Any:
            from onb_data import current_tenant

            if current_tenant().org_id == "org_a":
                raise RuntimeError("bus down")
            return await super().publish(event_type, payload, **kwargs)

    bus = FailingInA()
    job = ApprovalSlaJob(memory_store(world.db), bus, POLICY, clock=world.clock)
    assert run(job.run_once()) == SlaReport(reminders=1, failures=1)
    assert [(p["org_id"], p["payload"]["approval_id"]) for p in bus.published] == [("org_b", in_b)]


# --- box 2: the thresholds come from config -------------------------------------------------------------


def test_the_sweep_follows_the_thresholds_in_the_config_file(tmp_path: Path) -> None:
    config = tmp_path / "approval_sla.yaml"
    config.write_text("remind_after_sla: 0.5\nescalate_after_sla: 1.5\nescalate_to_role: HR_ADMIN\n")
    policy = load_sla_policy(config)
    world = World()
    approval_id = world.open(_spec(sla_hours=10))
    job = world.job(policy)

    world.clock.at(4.99)
    assert run(job.run_once()) == SlaReport()
    world.clock.at(5)  # 0.5 x 10 h
    assert run(job.run_once()) == SlaReport(reminders=1)
    world.clock.at(14.99)
    assert run(job.run_once()) == SlaReport()
    world.clock.at(15)  # 1.5 x 10 h
    assert run(job.run_once()) == SlaReport(escalations=1)
    assert world.stages() == [("org_a", approval_id, "reminder"), ("org_a", approval_id, "escalation")]
    assert world.bus.published[-1]["payload"]["escalate_to"] == "HR_ADMIN"


# --- box 3: no repeat after a worker restart --------------------------------------------------------------


def test_a_restarted_worker_does_not_remind_or_escalate_again() -> None:
    world = World()
    approval_id = world.open(_spec(sla_hours=10))

    world.clock.at(10)
    assert run(world.job().run_once()) == SlaReport(reminders=1)
    # the worker restarts: a new process, a new store, a new job, nothing kept in memory
    for hours in (10, 11, 19):
        world.clock.at(hours)
        assert run(world.job().run_once()) == SlaReport()
    world.clock.at(20)
    assert run(world.job().run_once()) == SlaReport(escalations=1)
    for hours in (20, 21, 100):  # and again after the escalation
        world.clock.at(hours)
        assert run(world.job().run_once()) == SlaReport()
    assert world.stages() == [("org_a", approval_id, "reminder"), ("org_a", approval_id, "escalation")]


def test_two_workers_sweeping_at_once_send_one_reminder() -> None:
    world = World()
    approval_id = world.open(_spec(sla_hours=10))
    world.clock.at(10)

    async def both() -> list[SlaReport]:
        return list(await asyncio.gather(world.job().run_once(), world.job().run_once()))

    reports = run(both())
    assert sum(r.reminders for r in reports) == 1
    assert world.stages() == [("org_a", approval_id, "reminder")]


def test_a_reminder_whose_event_was_published_but_not_stamped_is_not_sent_again() -> None:
    # The stand-in has no transaction; if a stamp were ever lost, the event's fixed key still stops a repeat.
    world = World()
    approval_id = world.open(_spec(sla_hours=10))
    world.clock.at(10)
    run(world.job().run_once())

    async def lose_the_stamp() -> None:
        with tenant_scope("org_a"):
            await (
                memory_store(world.db)
                .tenant("approvals")
                .update_one({"_id": approval_id}, {"$set": {"reminded_at": None}})
            )

    run(lose_the_stamp())
    world.clock.at(11)
    assert run(world.job().run_once()) == SlaReport()
    assert world.stages() == [("org_a", approval_id, "reminder")]


# --- the schedule ----------------------------------------------------------------------------------------


def test_the_loop_runs_a_pass_per_interval_until_stopped_without_real_waiting() -> None:
    world = World()
    world.open(_spec(sla_hours=10))
    stop = asyncio.Event()
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        world.clock.now += timedelta(seconds=seconds)
        if len(waits) == 3:
            stop.set()

    world.clock.at(10)
    reports = run(run_sla_forever(world.job(), interval=3600, stop=stop, sleep=fake_sleep))
    assert waits == [3600, 3600, 3600]
    assert reports[0] == SlaReport(reminders=1)
    assert sum(r.reminders for r in reports) == 1


def test_a_pass_that_raises_is_logged_and_the_loop_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[int] = []
    stop = asyncio.Event()

    class Flaky:
        async def run_once(self) -> SlaReport:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("mongo blip")
            stop.set()
            return SlaReport()

    async def no_sleep(_: float) -> None:
        return None

    run(run_sla_forever(Flaky(), interval=1, stop=stop, sleep=no_sleep))
    assert len(calls) == 2
    assert "approval sla pass failed: RuntimeError" in caplog.text
