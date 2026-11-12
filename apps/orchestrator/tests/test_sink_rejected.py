"""`task.sink_rejected` fails the task (NOTIF-1, ADR 0013; RUN-3).

A sink that refuses a grant sends nothing and publishes `task.sink_rejected {run_id, agent, task_id?,
sink, reason}`. The orchestrator's `orchestrator.fail_task` consumer marks the task `failed`, with the
reason code on the task, and leaves every other task, run and org alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from onb_agents.runs import FAIL_TASK, SINK_REJECTED, RunState, TaskState
from onb_contracts.notify import SINK_REJECTED as CONTRACT_SINK_REJECTED
from onb_contracts.notify import SINK_REJECTED_CONSUMER
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_orchestrator.consumer import fail_task_handler

from .agent_support import OK, Harness, Models, Tools, final, task_doc, template

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def job(run_id: str, payload: dict[str, Any], org: str = "org_a") -> JobContext:
    return JobContext(
        job_id="job_r",
        org_id=org,
        consumer=FAIL_TASK,
        event_id="ev_r",
        event_type=SINK_REJECTED,
        payload=MappingProxyType({"run_id": run_id, **payload}),
        run_id=run_id,
        attempt=1,
    )


def rejected(task_id: str | None = "notify_hire", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"agent": "db_agent", "sink": "notify.telegram", "reason": "wrong_run", **extra}
    if task_id is not None:
        payload["task_id"] = task_id
    return payload


async def deliver(h: Harness, run_id: str, payload: dict[str, Any], org: str = "org_a") -> None:
    handler = fail_task_handler(h.store)
    with tenant_scope(org):
        await handler(job(run_id, payload, org))


async def start_running(h: Harness, *tasks: dict[str, Any]) -> str:
    """A run whose tasks `a`... are `running`, as when their agents are mid-task."""
    run_id = await h.start(template(*tasks))
    with tenant_scope("org_a"):
        await h.runs.move_run(run_id, RunState.RUNNING, at=NOW)
        for t in tasks:
            record = await h.runs.task(run_id, t["id"])
            assert record is not None
            ready = await h.runs.move_task(run_id, t["id"], TaskState.READY, at=NOW, expected=record)
            await h.runs.move_task(run_id, t["id"], TaskState.RUNNING, at=NOW, expected=ready)
    return run_id


def harness(tmp_path: Path) -> Harness:
    return Harness.create(Models([final(OK)], tmp_path))


def test_the_consumer_names_and_events_are_the_notify_contracts() -> None:
    assert SINK_REJECTED == CONTRACT_SINK_REJECTED == "task.sink_rejected"
    assert FAIL_TASK == SINK_REJECTED_CONSUMER == "orchestrator.fail_task"


def test_a_running_task_is_failed_with_the_sinks_reason(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire"), task_doc("other")))
    run(deliver(h, run_id, rejected("notify_hire")))
    failed = run(h.task(run_id, "notify_hire"))
    assert failed["status"] == "failed"
    assert failed["failure"] == {"kind": "sink_rejected", "reason": "wrong_run"}
    assert run(h.task(run_id, "other"))["status"] == "running"  # a sibling is not touched


def test_a_finished_task_stays_finished(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire")))

    async def finish() -> None:
        with tenant_scope("org_a"):
            record = await h.runs.task(run_id, "notify_hire")
            assert record is not None
            await h.runs.move_task(run_id, "notify_hire", TaskState.DONE, at=NOW, expected=record)

    run(finish())
    run(deliver(h, run_id, rejected("notify_hire")))
    assert run(h.task(run_id, "notify_hire"))["status"] == "done"
    assert run(h.task(run_id, "notify_hire")).get("failure") is None


def test_a_rejection_without_a_task_id_fails_the_running_tasks_of_that_agent_only(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("one"), task_doc("two"), task_doc("prov", agent="provisioning_agent")))
    run(deliver(h, run_id, rejected(None)))
    assert [run(h.task(run_id, t))["status"] for t in ("one", "two", "prov")] == ["failed", "failed", "running"]


def test_a_redelivered_event_changes_nothing_more(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire")))
    run(deliver(h, run_id, rejected()))
    first = run(h.task(run_id, "notify_hire"))
    run(deliver(h, run_id, rejected()))
    assert run(h.task(run_id, "notify_hire")) == first


def test_another_orgs_run_is_nothing_to_do(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire")))
    run(deliver(h, run_id, rejected(), org="org_b"))  # org B's job names org A's run id
    assert run(h.task(run_id, "notify_hire"))["status"] == "running"


def test_a_task_that_does_not_exist_is_nothing_to_do(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire")))
    run(deliver(h, run_id, rejected("no_such_task")))
    assert run(h.task(run_id, "notify_hire"))["status"] == "running"


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "notify_hire", "reason": "x" * 80},
        {"task_id": "notify_hire", "reason": "Has Spaces"},
        {"task_id": "notify_hire", "reason": ""},
        {"task_id": 7, "reason": "wrong_run"},
    ],
)
def test_a_malformed_payload_is_refused_and_changes_nothing(tmp_path: Path, payload: dict[str, Any]) -> None:
    h = harness(tmp_path)
    run_id = run(start_running(h, task_doc("notify_hire")))
    with pytest.raises(ValueError, match=r"task\.sink_rejected"):
        run(deliver(h, run_id, {"agent": "db_agent", "sink": "notify.telegram", **payload}))
    assert run(h.task(run_id, "notify_hire"))["status"] == "running"


def test_a_task_failed_by_a_rejection_while_its_agent_runs_ends_failed_and_fails_the_run(tmp_path: Path) -> None:
    """The sink's rejection arrives while the agent is still mid-task: the agent's late success never
    turns the task `done`, the reason stays on the task, and the run fails."""
    h = harness(tmp_path)
    run_id_box: list[str] = []

    async def reject(_task: Any) -> None:
        await deliver(h, run_id_box[0], rejected("notify_hire"))

    h.tools.on_open = reject
    run_id = run(h.start(template(task_doc("notify_hire"), task_doc("after", "notify_hire"))))
    run_id_box.append(run_id)
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "notify_hire"))
    assert record["status"] == "failed"
    assert record["failure"] == {"kind": "sink_rejected", "reason": "wrong_run"}
    assert "result" not in record  # the agent's late outcome is not written onto a task failed from outside
    assert run(h.task(run_id, "after"))["status"] == "cancelled"
    assert run(h.run(run_id))["status"] == RunState.FAILED.value
    assert isinstance(h.tools, Tools)


def test_an_agents_own_failure_never_overwrites_the_sinks_cause(tmp_path: Path) -> None:
    """The agent fails too (bad output) after the sink rejected: the task keeps the sink's reason."""
    h = Harness.create(Models([{"content": "not json"}, {"content": "still not json"}], tmp_path))
    run_id_box: list[str] = []

    async def reject(_task: Any) -> None:
        await deliver(h, run_id_box[0], rejected("notify_hire"))

    h.tools.on_open = reject
    run_id = run(h.start(template(task_doc("notify_hire"))))
    run_id_box.append(run_id)
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "notify_hire"))
    assert record["status"] == "failed"
    assert record["failure"] == {"kind": "sink_rejected", "reason": "wrong_run"}
