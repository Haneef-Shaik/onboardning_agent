"""HR pauses and resumes a run back to back while a worker drives it (FIX-1; PRD FR-R5).

PERF-1 found a run failed by the worker (`TaskCancelled`) after a pause and a resume about 8 ms apart
(`docs/evidence/PERF-1/pause-resume-run-failed.txt`): the driving delivery saw the run paused, stopped,
then found it `running` again and took that for a task cancelled under a running run. A pause and a
resume never fail or cancel anything: the resume's own `run.resumed` delivery drives the run on.

Each pause → resume pair here is what `POST /runs/{id}/pause` then `POST /runs/{id}/resume` do to the run
(the resume publishes `run.resumed`, which the test delivers like the bus would). The pair lands at every
read of the run a delivery makes, with no delay between pause and resume, and from a concurrent toggler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final

import pytest
from onb_agents.approvals import ApprovalDecider, ApprovalStore
from onb_agents.runs import RUN_RESUMED, RUN_STARTED, START_RUN, RunStarter, RunState, RunStore
from onb_agents.runs.testing import OutboxStandIn, diamond_template, hire_variables_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.notify import Decision
from onb_contracts.templates import Template
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.policy import RunPolicy

ORG: Final = "org_a"
HR: Final = "u_hr"
MANAGER: Final = "u_test_manager"
NOW: Final = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
NO_WAIT: Final = RunPolicy(max_attempts=3, backoff_base=0.0, backoff_max=0.0, lease_seconds=30.0)
# Every read of the run a single drive of the diamond makes is below this; the sweep covers them all.
READS_SWEPT: Final = 40
PAIRS_PER_READ: Final = 3
NEVER: Final = frozenset({"failed", "cancelled"})


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _approval_template() -> Template:
    def task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
        return {
            "id": task_id,
            "title": f"Task {task_id}",
            "agent": "provisioning_agent",
            "action": f"do_{task_id}",
            "depends_on": list(depends_on),
            **extra,
        }

    approval = task(
        "appr",
        "a",
        type="approval",
        agent="approvals_agent",
        action="approval",
        approver={"rule": "variable", "ref": "manager_id"},
        sla_hours=48,
    )
    return Template.model_validate(
        {
            "key": "pause-race",
            "name": "Pause race (test)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": [task("a"), approval, task("b", "appr"), task("c", "a")],
        }
    )


class World:
    def __init__(self) -> None:
        self.store: DataStore = memory_store(MemoryDatabase())
        self.runs = RunStore(self.store)
        self.bus = OutboxStandIn()
        self.approvals = ApprovalStore(self.store)
        self.resumes = 0  # `run.resumed` events HR's resumes published and the bus has not delivered yet

    async def start(self, template: Template) -> str:
        with tenant_scope(ORG):
            await self.store.tenant("new_hires").insert_one({"_id": "h_1", "manager_id": MANAGER})
            started = await RunStarter(self.runs, self.bus, InMemoryAuditLog()).start(
                template=template, hire_id="h_1", variables=hire_variables_for(), actor_id=HR
            )
        return str(started.run["_id"])

    async def pause_resume(self, run_id: str, original_get: Any) -> bool:
        """HR's pause then resume, no delay between them. False when the run was not running."""
        current = await original_get(self.runs, run_id)
        if current is None or current["status"] != RunState.RUNNING.value:
            return False
        await self.runs.move_run(run_id, RunState.PAUSED, at=NOW, by=HR)
        await self.runs.move_run(run_id, RunState.RUNNING, at=NOW, by=HR)
        self.resumes += 1
        return True

    async def deliver(self, run_id: str, event: str) -> None:
        handler = start_run_handler(self.store, StubExecutor(), bus=self.bus, policy=NO_WAIT, clock=lambda: NOW)
        consumer = START_RUN if event == RUN_STARTED else "orchestrator.resume_run"
        job = JobContext("job", ORG, consumer, "ev", event, MappingProxyType({"run_id": run_id}), run_id, 1)
        with tenant_scope(ORG):
            await handler(job)

    async def drain_resumes(self, run_id: str) -> None:
        """The bus delivers every `run.resumed` the resumes published (at least once, in order)."""
        while self.resumes:
            self.resumes -= 1
            await self.deliver(run_id, RUN_RESUMED)

    async def state(self, run_id: str) -> tuple[str, dict[str, str], list[str]]:
        with tenant_scope(ORG):
            found = await self.runs.get(run_id)
            tasks = {t["task_id"]: str(t["status"]) for t in await self.runs.tasks(run_id)}
            approvals = [str(a["status"]) for a in await self.approvals.for_task(run_id, "appr")]
        assert found is not None
        return str(found["status"]), tasks, approvals


def _pair_at_read(world: World, run_id: str, target: int, pairs: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """At the `target`-th read of the run, HR pauses it just before the read lands and resumes it right after
    (`pairs` times), so the reader sees `paused` and the next reader sees `running`."""
    original = RunStore.get
    reads = [0]

    async def get(self: RunStore, rid: str) -> dict[str, Any] | None:
        index = reads[0]
        reads[0] += 1
        if rid != run_id or index != target:
            return await original(self, rid)
        current = await original(self, rid)
        if current is None or current["status"] != RunState.RUNNING.value:
            return current
        found: dict[str, Any] | None = current
        for _ in range(pairs):
            await self.move_run(rid, RunState.PAUSED, at=NOW, by=HR)
            found = await original(self, rid)  # this delivery reads `paused`
            await self.move_run(rid, RunState.RUNNING, at=NOW, by=HR)  # resumed with no delay
            world.resumes += 1
        return found

    monkeypatch.setattr(RunStore, "get", get)


def _assert_never_failed(status: str, tasks: dict[str, str], approvals: list[str], label: str) -> None:
    assert status not in NEVER, f"{label}: the run ended {status}; tasks {tasks}"
    assert not NEVER & set(tasks.values()), f"{label}: a task was failed or cancelled: {tasks}"
    assert "cancelled" not in approvals, f"{label}: the pending approval was cancelled with the run"


@pytest.mark.parametrize("pairs", [1, PAIRS_PER_READ])
def test_a_pause_and_resume_at_any_read_of_the_run_never_fails_it(pairs: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sweep the pair over every read of the run one drive makes: the run always completes."""
    for target in range(READS_SWEPT):
        world = World()

        async def go(w: World = world, at: int = target) -> tuple[str, dict[str, str], list[str]]:
            run_id = await w.start(diamond_template())
            with monkeypatch.context() as patch:
                _pair_at_read(w, run_id, at, pairs, patch)
                await w.deliver(run_id, RUN_STARTED)
                status, tasks, approvals = await w.state(run_id)
                _assert_never_failed(status, tasks, approvals, f"read {at}, during the drive")
            await w.drain_resumes(run_id)
            return await w.state(run_id)

        status, tasks, approvals = run(go())
        _assert_never_failed(status, tasks, approvals, f"read {target}")
        assert status == "completed", f"read {target}: {status} {tasks}"


@pytest.mark.parametrize("pairs", [1, PAIRS_PER_READ])
def test_a_pause_and_resume_while_an_approval_is_pending_keeps_the_approval_and_the_tasks(
    pairs: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PERF-1 case: a task waits for an approval, HR pauses and resumes, the resume's drive meets the next
    pause. The approval stays pending, no task is cancelled, and the decision still completes the run."""
    for target in range(READS_SWEPT):
        world = World()

        async def go(w: World = world, at: int = target) -> tuple[str, dict[str, str], list[str]]:
            run_id = await w.start(_approval_template())
            await w.deliver(run_id, RUN_STARTED)  # parks at the approval, run stays running
            with monkeypatch.context() as patch:
                with tenant_scope(ORG):
                    assert await w.pause_resume(run_id, RunStore.get)
                _pair_at_read(w, run_id, at, pairs, patch)
                await w.drain_resumes(run_id)
                status, tasks, approvals = await w.state(run_id)
                _assert_never_failed(status, tasks, approvals, f"read {at}")
            assert approvals == ["pending"], f"read {at}: {approvals}"
            with tenant_scope(ORG):
                [approval] = await w.approvals.for_task(run_id, "appr")
                decider = ApprovalDecider(w.approvals, w.bus, clock=lambda: NOW)
                await decider.decide(
                    approval["_id"], decision=Decision.APPROVE, comment=None, channel="web", user_id=MANAGER
                )
            await w.deliver(run_id, RUN_RESUMED)  # drives the run on and settles the decided approval
            return await w.state(run_id)

        status, tasks, approvals = run(go())
        _assert_never_failed(status, tasks, approvals, f"read {target}")
        assert status == "completed", f"read {target}: {status} {tasks}"


def test_many_pause_resume_pairs_racing_the_worker_never_fail_the_run() -> None:
    """A concurrent toggler pauses and resumes the run back to back, with no delay and with one-tick delays,
    while the deliveries drive it. Many runs, many pairs each: none is ever failed or cancelled."""
    runs_tried = 25

    async def one(round_no: int) -> tuple[str, dict[str, str], list[str], int]:
        world = World()
        run_id = await world.start(diamond_template())
        executor_delay_runs = round_no % 2 == 1
        stop = asyncio.Event()
        toggled = [0]

        async def toggler() -> None:
            while not stop.is_set():
                with tenant_scope(ORG):
                    if await world.pause_resume(run_id, RunStore.get):
                        toggled[0] += 1
                await asyncio.sleep(0 if round_no % 3 else 0.0005)

        async def drive(event: str) -> None:
            handler = start_run_handler(
                world.store,
                StubExecutor(delay=0.001 if executor_delay_runs else 0.0),
                bus=world.bus,
                policy=NO_WAIT,
                clock=lambda: NOW,
            )
            consumer = START_RUN if event == RUN_STARTED else "orchestrator.resume_run"
            job = JobContext("job", ORG, consumer, "ev", event, MappingProxyType({"run_id": run_id}), run_id, 1)
            with tenant_scope(ORG):
                await handler(job)

        racing = asyncio.ensure_future(toggler())
        await drive(RUN_STARTED)
        for _ in range(10):  # a few resume deliveries race the toggler too
            if not world.resumes:
                break
            world.resumes -= 1
            await drive(RUN_RESUMED)
        stop.set()
        await racing
        status, tasks, approvals = await world.state(run_id)
        _assert_never_failed(status, tasks, approvals, f"round {round_no}, while racing")
        while world.resumes:
            world.resumes -= 1
            await drive(RUN_RESUMED)
        status, tasks, approvals = await world.state(run_id)
        return status, tasks, approvals, toggled[0]

    total_pairs = 0
    for round_no in range(runs_tried):
        status, tasks, approvals, toggled = run(one(round_no))
        total_pairs += toggled
        _assert_never_failed(status, tasks, approvals, f"round {round_no}")
        assert status == "completed", f"round {round_no}: {status} {tasks}"
    assert total_pairs >= runs_tried, "the toggler must actually have raced the worker"
