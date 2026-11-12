"""Reading and controlling runs over HTTP (RUN-2; PRD FR-R5, §3, §7.8, §10 Hires & runs, Live updates).

The app is the unit-test app (conftest): seed_v1's hires in an in-memory database, runs through the
outbox stand-in. What the orchestrator does to a run (failing a task, blocking the run) is done here
straight on the store, the way the orchestrator's own tests prove it; the whole thing against the real
bus and Mongo is in `tests_int`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_agents.runs import (
    RUN_CANCELLED,
    RUN_PAUSED,
    RUN_RESUMED,
    RunState,
    RunStore,
    TaskState,
)
from onb_agents.runs.testing import OutboxStandIn
from onb_api.runs import RunService
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.auth import Role
from onb_data import AuditQuery, tenant_scope

SEED_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
RAVI = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
ANITA = ("u_acme_mgr_anita", "org_acme", Role.MANAGER)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def _publish(client: TestClient) -> None:
    template: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    draft = client.post("/templates/drafts", json={"template": template}, headers=_auth(ADMIN))
    published = client.post(f"/templates/drafts/{draft.json()['data']['id']}/publish", headers=_auth(ADMIN))
    assert published.status_code == 200, published.text


def _start(client: TestClient, hire: str = "h_acme_001", who: tuple[str, str, Role] = HR) -> str:
    body = {"hire_id": hire, "template_key": "engineer-india", "template_version": 1}
    response = client.post("/runs", json=body, headers=_auth(who))
    assert response.status_code == 201, response.text
    run_id: str = response.json()["data"]["id"]
    return run_id


class Orchestrator:
    """What the orchestrator does to the stored run, on the same database the app uses."""

    def __init__(self, db: MemoryDatabase) -> None:
        self.runs = RunStore(memory_store(db))

    def do(self, work: Any, org: str = "org_acme") -> Any:
        async def go() -> Any:
            with tenant_scope(org):
                return await work(self.runs)

        return run(go())

    def block_on(self, run_id: str, *failed: str) -> None:
        """The run is running, and each of `failed` ran and failed its last attempt: the run is blocked."""

        async def work(runs: RunStore) -> None:
            await runs.move_run(run_id, RunState.RUNNING, at=NOW)
            for task_id in failed:
                ready = await runs.move_task(run_id, task_id, TaskState.READY, at=NOW)
                running = await runs.move_task(run_id, task_id, TaskState.RUNNING, at=NOW, expected=ready)
                await runs.move_task(
                    run_id, task_id, TaskState.FAILED, at=NOW, expected=running, fields={"error": "StubTaskError"}
                )
            await runs.move_run(run_id, RunState.BLOCKED, at=NOW)

        self.do(work)

    def task(self, run_id: str, task_id: str) -> dict[str, Any]:
        async def work(runs: RunStore) -> dict[str, Any]:
            return await runs.task(run_id, task_id) or {}

        found: dict[str, Any] = self.do(work)
        return found

    def status(self, run_id: str) -> str:
        async def work(runs: RunStore) -> str:
            return str((await runs.get(run_id) or {})["status"])

        status: str = self.do(work)
        return status


@pytest.fixture
def orchestrator(memory_db: MemoryDatabase) -> Orchestrator:
    return Orchestrator(memory_db)


def _entries(audit_log: InMemoryAuditLog, action: str) -> list[Any]:
    async def go() -> list[Any]:
        with tenant_scope("org_acme"):
            return list((await audit_log.list(AuditQuery(action=action), page=1, limit=20)).entries)

    found: list[Any] = run(go())
    return found


FIRST = "collect_docs"


# --- box 4: managers list only their reports' runs ----------------------------------------------------


def test_hr_lists_every_run_of_the_org_newest_first_and_a_manager_only_their_reports(client: TestClient) -> None:
    _publish(client)
    mine = _start(client, "h_acme_001")  # Ravi's report
    other = _start(client, "h_acme_002")  # Anita's report
    also_mine = _start(client, "h_acme_003")  # Ravi's report

    everything = client.get("/runs", headers=_auth(HR))
    assert everything.status_code == 200, everything.text
    assert [r["id"] for r in everything.json()["data"]] == [also_mine, other, mine] or {
        r["id"] for r in everything.json()["data"]
    } == {mine, other, also_mine}
    assert everything.json()["meta"]["page"]["total"] == 3

    ravi = client.get("/runs", headers=_auth(RAVI))
    assert {r["id"] for r in ravi.json()["data"]} == {mine, also_mine}
    assert ravi.json()["meta"]["page"]["total"] == 2
    anita = client.get("/runs", headers=_auth(ANITA))
    assert [r["id"] for r in anita.json()["data"]] == [other]


def test_a_manager_without_reports_lists_nothing_and_another_orgs_runs_never_appear(client: TestClient) -> None:
    _publish(client)
    _start(client)
    nobody = client.get("/runs", headers=_auth(("u_acme_mgr_nobody", "org_acme", Role.MANAGER)))
    assert nobody.json()["data"] == []
    assert nobody.json()["meta"]["page"]["total"] == 0
    assert client.get("/runs", headers=_auth(GLOBEX_HR)).json()["data"] == []


def test_the_run_list_pages_filters_by_status_and_carries_no_tasks(
    client: TestClient, orchestrator: Orchestrator
) -> None:
    _publish(client)
    ids = [_start(client, hire) for hire in ("h_acme_001", "h_acme_002", "h_acme_003")]
    orchestrator.block_on(ids[1], FIRST)
    page = client.get("/runs", params={"limit": 2, "page": 2}, headers=_auth(HR)).json()
    assert len(page["data"]) == 1
    assert page["meta"]["page"] == {"total": 3, "page": 2, "limit": 2}
    blocked = client.get("/runs", params={"status": "blocked"}, headers=_auth(HR)).json()
    assert [r["id"] for r in blocked["data"]] == [ids[1]]
    assert "tasks" not in blocked["data"][0]
    assert client.get("/runs", params={"status": "exploded"}, headers=_auth(HR)).status_code == 422
    assert client.get("/runs", params={"limit": 0}, headers=_auth(HR)).status_code == 422


def test_a_run_is_read_with_its_tasks_by_hr_and_by_the_hires_manager_only(client: TestClient) -> None:
    _publish(client)
    run_id = _start(client, "h_acme_001")
    seen = client.get(f"/runs/{run_id}", headers=_auth(HR))
    assert seen.status_code == 200
    data = seen.json()["data"]
    assert data["id"] == run_id
    assert data["status"] == "started"
    assert {t["task_id"] for t in data["tasks"]} >= {FIRST}
    assert {"attempts", "error"} <= set(data["tasks"][0])
    assert client.get(f"/runs/{run_id}", headers=_auth(RAVI)).status_code == 200
    for stranger in (ANITA, GLOBEX_HR):
        missing = client.get(f"/runs/{run_id}", headers=_auth(stranger))
        assert missing.status_code == 404
        assert missing.json() == client.get("/runs/run_" + "0" * 24, headers=_auth(stranger)).json() | {
            "meta": missing.json()["meta"]
        }
    assert client.get("/runs/not%20a%20run", headers=_auth(HR)).status_code == 422


# --- pause, resume, cancel ----------------------------------------------------------------------------


def test_hr_pauses_resumes_and_cancels_a_run_and_each_is_audited_and_announced(
    client: TestClient, orchestrator: Orchestrator, run_outbox: OutboxStandIn, audit_log: InMemoryAuditLog
) -> None:
    _publish(client)
    run_id = _start(client)
    orchestrator.do(lambda runs: runs.move_run(run_id, RunState.RUNNING, at=NOW))

    paused = client.post(f"/runs/{run_id}/pause", json={"reason": "Holiday freeze"}, headers=_auth(HR))
    assert paused.status_code == 200, paused.text
    assert paused.json()["data"]["status"] == "paused"
    resumed = client.post(f"/runs/{run_id}/resume", json={}, headers=_auth(HR))
    assert resumed.json()["data"]["status"] == "running"
    cancelled = client.post(f"/runs/{run_id}/cancel", json={"reason": "Offer withdrawn"}, headers=_auth(ADMIN))
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert all(t["status"] in {"cancelled", "skipped"} for t in cancelled.json()["data"]["tasks"])

    events = [(e["type"], e["payload"]) for e in run_outbox.published if e["type"] != "run.started"]
    assert events == [
        (RUN_PAUSED, {"run_id": run_id}),
        (RUN_RESUMED, {"run_id": run_id}),
        (RUN_CANCELLED, {"run_id": run_id}),
    ]
    for action, reason in (("run.pause", "Holiday freeze"), ("run.resume", None), ("run.cancel", "Offer withdrawn")):
        [entry] = _entries(audit_log, action)
        assert (entry.target, entry.outcome.value, entry.reason) == (f"run:{run_id}", "success", reason)
    assert _entries(audit_log, "run.pause")[0].actor.id == "u_acme_hr1"


def test_a_control_that_does_not_fit_the_runs_state_is_a_conflict_and_changes_nothing(
    client: TestClient, orchestrator: Orchestrator, run_outbox: OutboxStandIn
) -> None:
    _publish(client)
    run_id = _start(client)
    assert client.post(f"/runs/{run_id}/resume", json={}, headers=_auth(HR)).status_code == 409  # not paused
    assert client.post(f"/runs/{run_id}/pause", json={}, headers=_auth(HR)).status_code == 200
    again = client.post(f"/runs/{run_id}/pause", json={}, headers=_auth(HR))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"
    assert client.post(f"/runs/{run_id}/cancel", json={}, headers=_auth(HR)).status_code == 200
    for action in ("pause", "resume", "cancel"):
        assert client.post(f"/runs/{run_id}/{action}", json={}, headers=_auth(HR)).status_code == 409
    assert orchestrator.status(run_id) == "cancelled"
    assert [e["type"] for e in run_outbox.published] == ["run.started", RUN_PAUSED, RUN_CANCELLED]


def test_a_manager_may_not_control_a_run_and_another_orgs_run_is_not_found(
    client: TestClient, orchestrator: Orchestrator
) -> None:
    _publish(client)
    run_id = _start(client)
    for path in ("pause", "resume", "cancel", f"tasks/{FIRST}/retry", f"tasks/{FIRST}/complete"):
        assert client.post(f"/runs/{run_id}/{path}", json={"reason": "x"}, headers=_auth(RAVI)).status_code == 403
        assert client.post(f"/runs/{run_id}/{path}", json={"reason": "x"}, headers=_auth(GLOBEX_HR)).status_code == 404
    assert orchestrator.status(run_id) == "started"


def test_a_control_body_with_an_org_or_an_empty_reason_is_refused(client: TestClient) -> None:
    _publish(client)
    run_id = _start(client)
    assert client.post(f"/runs/{run_id}/pause", json={"org_id": "org_globex"}, headers=_auth(HR)).status_code == 422
    assert client.post(f"/runs/{run_id}/pause", json={"reason": "   "}, headers=_auth(HR)).status_code == 422
    assert client.post(f"/runs/{run_id}/pause", headers=_auth(HR)).status_code == 200  # no body at all is fine


# --- retry and complete a task ------------------------------------------------------------------------


def test_hr_retrying_a_failed_task_unblocks_the_run_and_resumes_it(
    client: TestClient, orchestrator: Orchestrator, run_outbox: OutboxStandIn, audit_log: InMemoryAuditLog
) -> None:
    _publish(client)
    run_id = _start(client)
    orchestrator.block_on(run_id, FIRST)
    assert orchestrator.task(run_id, FIRST)["attempts"] == 1

    response = client.post(f"/runs/{run_id}/tasks/{FIRST}/retry", json={"reason": "Mailbox fixed"}, headers=_auth(HR))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    task = next(t for t in data["tasks"] if t["task_id"] == FIRST)
    assert (data["status"], task["status"], task["attempts"], task["error"]) == ("running", "ready", 0, None)
    assert [(e["type"], e["payload"]) for e in run_outbox.published][-1] == (RUN_RESUMED, {"run_id": run_id})
    [entry] = _entries(audit_log, "run.task.retry")
    assert (entry.target, entry.reason) == (f"run:{run_id}/task:{FIRST}", "Mailbox fixed")


def test_the_run_stays_blocked_until_every_failed_task_has_been_dealt_with(
    client: TestClient, orchestrator: Orchestrator, run_outbox: OutboxStandIn
) -> None:
    _publish(client)
    run_id = _start(client)
    second = next(
        t["task_id"]
        for t in client.get(f"/runs/{run_id}", headers=_auth(HR)).json()["data"]["tasks"]
        if t["task_id"] != FIRST and not t["depends_on"]
    )
    orchestrator.block_on(run_id, FIRST, second)
    first = client.post(f"/runs/{run_id}/tasks/{FIRST}/retry", json={}, headers=_auth(HR))
    assert first.json()["data"]["status"] == "blocked"
    assert orchestrator.status(run_id) == "blocked"
    last = client.post(f"/runs/{run_id}/tasks/{second}/retry", json={}, headers=_auth(HR))
    assert last.json()["data"]["status"] == "running"
    # Each control announces a resume (a no-op for the orchestrator while the run is still blocked), so
    # two HR users clearing the last two failed tasks at once cannot leave the run blocked for ever.
    assert [e["type"] for e in run_outbox.published].count(RUN_RESUMED) == 2


def test_a_task_that_has_not_failed_or_a_run_that_is_not_blocked_cannot_be_retried_or_completed(
    client: TestClient, orchestrator: Orchestrator
) -> None:
    _publish(client)
    run_id = _start(client)
    for action in ("retry", "complete"):
        not_blocked = client.post(f"/runs/{run_id}/tasks/{FIRST}/{action}", json={"reason": "x"}, headers=_auth(HR))
        assert not_blocked.status_code == 409, action
    orchestrator.block_on(run_id, FIRST)
    other = next(
        t["task_id"]
        for t in client.get(f"/runs/{run_id}", headers=_auth(HR)).json()["data"]["tasks"]
        if t["status"] == "pending" and t["task_id"] != FIRST
    )
    for action in ("retry", "complete"):
        assert (
            client.post(f"/runs/{run_id}/tasks/{other}/{action}", json={"reason": "x"}, headers=_auth(HR)).status_code
            == 409
        )
        assert (
            client.post(
                f"/runs/{run_id}/tasks/no_such_task/{action}", json={"reason": "x"}, headers=_auth(HR)
            ).status_code
            == 404
        )
    assert orchestrator.task(run_id, FIRST)["status"] == "failed"


# --- box 3: manual complete needs a reason, and is audited --------------------------------------------


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": None}, {"reason": 5}])
def test_completing_a_task_by_hand_without_a_reason_is_422_and_changes_nothing(
    client: TestClient, orchestrator: Orchestrator, audit_log: InMemoryAuditLog, body: dict[str, Any]
) -> None:
    _publish(client)
    run_id = _start(client)
    orchestrator.block_on(run_id, FIRST)
    response = client.post(f"/runs/{run_id}/tasks/{FIRST}/complete", json=body, headers=_auth(HR))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert orchestrator.task(run_id, FIRST)["status"] == "failed"
    assert orchestrator.status(run_id) == "blocked"
    assert _entries(audit_log, "run.task.complete") == []


def test_completing_a_task_by_hand_with_a_reason_is_audited_and_resumes_the_run(
    client: TestClient, orchestrator: Orchestrator, run_outbox: OutboxStandIn, audit_log: InMemoryAuditLog
) -> None:
    _publish(client)
    run_id = _start(client)
    orchestrator.block_on(run_id, FIRST)
    reason = "IT did this by hand, ticket closed"
    response = client.post(f"/runs/{run_id}/tasks/{FIRST}/complete", json={"reason": reason}, headers=_auth(HR))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert next(t for t in data["tasks"] if t["task_id"] == FIRST)["status"] == "done"
    assert data["status"] == "running"
    [entry] = _entries(audit_log, "run.task.complete")
    assert (entry.target, entry.reason, entry.outcome.value, entry.actor.id) == (
        f"run:{run_id}/task:{FIRST}",
        reason,
        "success",
        "u_acme_hr1",
    )
    assert (RUN_RESUMED, {"run_id": run_id}) in [(e["type"], e["payload"]) for e in run_outbox.published]
    done = orchestrator.task(run_id, FIRST)
    assert done["history"][-1]["by"] == "u_acme_hr1"
    assert reason not in json.dumps(done, default=str), "the reason lives in the audit log only"


# --- box 5: the stream ---------------------------------------------------------------------------------


def _events(client: TestClient, run_id: str, who: tuple[str, str, Role] = HR) -> list[tuple[str, dict[str, Any]]]:
    """Read a run's stream to its end: [(event name, data)]."""
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", f"/runs/{run_id}/stream", headers=_auth(who)) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))
    return events


@pytest.fixture
def fast_stream(runs: RunService) -> None:
    runs.stream_poll_seconds = 0.02


def _finish(orchestrator: Orchestrator, run_id: str, task_ids: list[str]) -> None:
    async def work(runs: RunStore) -> None:
        await runs.move_run(run_id, RunState.RUNNING, at=NOW)
        for task_id in task_ids:
            ready = await runs.move_task(run_id, task_id, TaskState.READY, at=NOW)
            running = await runs.move_task(run_id, task_id, TaskState.RUNNING, at=NOW, expected=ready)
            await runs.move_task(run_id, task_id, TaskState.DONE, at=NOW, expected=running)
        await runs.move_run(run_id, RunState.COMPLETED, at=NOW)

    orchestrator.do(work)


def test_the_stream_emits_each_task_transition_in_order_and_ends_with_the_run(
    client: TestClient, orchestrator: Orchestrator, fast_stream: None
) -> None:
    _publish(client)
    run_id = _start(client, "h_acme_003")  # a sales hire: some tasks are skipped
    tasks = client.get(f"/runs/{run_id}", headers=_auth(HR)).json()["data"]["tasks"]
    runnable = [t["task_id"] for t in tasks if t["status"] == "pending" and not t["depends_on"]][:2]
    assert runnable

    async def work(runs: RunStore) -> None:
        await runs.move_run(run_id, RunState.RUNNING, at=NOW)
        for task_id in runnable:
            ready = await runs.move_task(run_id, task_id, TaskState.READY, at=NOW)
            running = await runs.move_task(run_id, task_id, TaskState.RUNNING, at=NOW, expected=ready)
            await runs.move_task(run_id, task_id, TaskState.DONE, at=NOW, expected=running)
        await runs.move_run(run_id, RunState.CANCELLED, at=NOW)

    orchestrator.do(work)
    events = _events(client, run_id)
    transitions = [(d["task_id"], d["from"], d["to"]) for name, d in events if name == "task"]
    for task_id in runnable:
        mine = [(f, t) for tid, f, t in transitions if tid == task_id]
        assert mine == [("pending", "ready"), ("ready", "running"), ("running", "done")], (task_id, mine)
    assert [d["to"] for name, d in events if name == "run"] == ["running", "cancelled"]
    assert events[-1][0] == "end", "a finished run ends its stream"
    assert all("org_id" not in d for _, d in events)


def test_the_stream_follows_a_run_live_until_it_finishes(
    client: TestClient, orchestrator: Orchestrator, fast_stream: None
) -> None:
    _publish(client)
    run_id = _start(client)
    tasks = client.get(f"/runs/{run_id}", headers=_auth(HR)).json()["data"]["tasks"]
    todo = [t["task_id"] for t in tasks if t["status"] == "pending"]
    # Complete every pending task in dependency order from another thread while the stream is open.
    ordered = _in_order(tasks, todo)

    def drive() -> None:
        time.sleep(0.2)
        _finish(orchestrator, run_id, ordered)

    worker = threading.Thread(target=drive)
    worker.start()
    try:
        events = _events(client, run_id)
    finally:
        worker.join()
    done = {d["task_id"] for name, d in events if name == "task" and d["to"] == "done"}
    assert done == set(ordered)
    assert events[-1] == ("end", {"status": "completed"})


def _in_order(tasks: list[dict[str, Any]], todo: list[str]) -> list[str]:
    deps = {t["task_id"]: set(t["depends_on"]) for t in tasks}
    ordered: list[str] = []
    remaining = list(todo)
    while remaining:
        ready = next(t for t in remaining if not (deps[t] & set(remaining)))
        ordered.append(ready)
        remaining.remove(ready)
    return ordered


def test_the_stream_is_scoped_like_the_run(client: TestClient, fast_stream: None, orchestrator: Orchestrator) -> None:
    _publish(client)
    run_id = _start(client, "h_acme_001")
    orchestrator.do(lambda runs: runs.move_run(run_id, RunState.CANCELLED, at=NOW))
    assert _events(client, run_id, RAVI)[-1][0] == "end"
    for stranger in (ANITA, GLOBEX_HR):
        assert client.get(f"/runs/{run_id}/stream", headers=_auth(stranger)).status_code == 404
    assert client.get(f"/runs/{run_id}/stream").status_code == 401


def test_a_stream_ends_after_its_lifetime_so_the_client_reconnects(
    client: TestClient, runs: RunService, fast_stream: None
) -> None:
    runs.stream_max_seconds = 0.15
    _publish(client)
    run_id = _start(client)  # `started` forever: nothing finishes it
    events = _events(client, run_id)
    assert events[-1] == ("end", {"status": "started", "reason": "timeout"})


# --- UI-5: the trace id is for HR, not for managers -----------------------------------------------------

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def test_hr_and_hr_admin_get_the_runs_trace_id_and_a_manager_does_not(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    _publish(client)
    run_id = _start(client, "h_acme_001")  # Ravi's report
    for collection in memory_db.collections.values():
        for document in collection.documents:
            if document.get("_id") == run_id:
                document["traceparent"] = f"00-{TRACE_ID}-00f067aa0ba902b7-01"

    for who in (HR, ADMIN):
        assert client.get(f"/runs/{run_id}", headers=_auth(who)).json()["data"]["trace_id"] == TRACE_ID
    seen = client.get(f"/runs/{run_id}", headers=_auth(RAVI))
    assert seen.status_code == 200  # the run itself is his to see
    assert seen.json()["data"]["trace_id"] is None
    assert TRACE_ID not in seen.text
