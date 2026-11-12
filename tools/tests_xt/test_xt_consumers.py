"""Every event consumer: handling an org-A event never reads, changes or reveals org B (PRD §7.9).

`CONSUMER_CASES` has one row per consumer in `surface.PLATFORM_SUBSCRIPTIONS`; `test_xt_coverage.py`
fails the run when a consumer has no row. RUN-1 adds the first real one, `orchestrator.start_run`. The
generic check also keeps running on `xt.probe`, a consumer of this suite that reads and writes through
the store like a real one, so the check itself stays exercised whatever the platform subscribes.

The generic check, over the real handler:
1. One org-A event, one worker run: the handler ran inside org A's tenant context, its `JobContext`
   says org A, and no org-B document (in any collection) changed.
2. The same idempotency key in org B is a new event, not a duplicate (no inference through the
   per-org unique index).
3. Org A and org B jobs queued together on one worker: every handler call runs in the org of its own
   job, so no tenant context or cached state carries over from the previous job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from onb_agents.approvals import ApprovalDecider, ApprovalStore
from onb_agents.runs import (
    CLEANUP_RUN,
    FAIL_TASK,
    RESUME_APPROVAL,
    RESUME_RUN,
    RUN_CANCELLED,
    RUN_RESUMED,
    RUN_STARTED,
    SINK_REJECTED,
    START_RUN,
)
from onb_contracts.notify import (
    APPROVAL_DECIDED,
    APPROVAL_NOTIFY_CONSUMER,
    APPROVAL_OVERDUE,
    APPROVAL_REQUESTED,
    APPROVAL_SLA_CONSUMER,
    CHANGE_REQUEST_DECIDED,
    DECISION_RECEIVED,
    IN_APP_CONSUMER,
    NOTIFY_SUBSCRIPTIONS,
    RECORD_DECISION_CONSUMER,
    RUN_BLOCKED,
    Decision,
)
from onb_data import AuditLog, DataStore, current_tenant, tenant_scope
from onb_eventbus import Handler, JobContext, MongoEventBus
from onb_mcp_mocks.notify.approvals import approval_notify_handler
from onb_mcp_mocks.notify.in_app import in_app_handler
from onb_mcp_mocks.notify.outbox import TelegramMockChannel
from onb_mcp_mocks.notify.sla import approval_sla_handler
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_orchestrator.consumer import (
    cleanup_run_handler,
    fail_task_handler,
    record_decision_handler,
    resume_approval_handler,
    start_run_handler,
)
from onb_orchestrator.executor import StubExecutor

from .conftest import Unscoped, World
from .world import ORG_A, ORG_B, XT_RUNS, seed_users, tenant_documents

# (tenant context during the handler, the job's org) for each handler call, in order.
Observed = list[tuple[str, str]]


@dataclass(frozen=True)
class ConsumerCase:
    event_type: str
    """An event type the consumer is subscribed to."""
    payload: Callable[[str], Mapping[str, Any]]
    """An ordinary payload for an org (ids and `ptk_` tokens only)."""
    handler: Callable[[DataStore], Handler]
    """The production handler, built over the store."""


def _start_run(store: DataStore) -> Handler:
    """The orchestrator's real consumer, with the stub executor RUN-1 ships (agents arrive in RUN-3)."""
    return start_run_handler(store, StubExecutor())


def _fail_task(store: DataStore) -> Handler:
    """The orchestrator's real `task.sink_rejected` consumer (RUN-3)."""
    return fail_task_handler(store)


def _resume_run(store: DataStore) -> Handler:
    """HIL-1: the orchestrator's real `approval.decided` consumer."""
    return resume_approval_handler(store, StubExecutor())


def _record_decision(store: DataStore) -> Handler:
    """HIL-1: the orchestrator's real `decision.received` consumer (a Telegram press on an approval)."""
    return record_decision_handler(store)


def _approval_notify(store: DataStore) -> Handler:
    """HIL-1: mcp-notify's real `approval.requested` consumer."""
    return approval_notify_handler(store, TelegramMockChannel(store, TelegramMockTransport()))


def _approval_sla(store: DataStore) -> Handler:
    """HIL-2: mcp-notify's real `approval.overdue` consumer (reminder / escalation)."""
    return approval_sla_handler(store, TelegramMockChannel(store, TelegramMockTransport()))


# The approval each org's events name: org A's is seeded by seed_v1, org B's by `world.EXTRA_DOCUMENTS`.
XT_APPROVALS = {ORG_A: "appr_acme_001", ORG_B: "appr_globex_xt"}

# One row per platform consumer.
CONSUMER_CASES: Mapping[str, ConsumerCase] = {
    # NOTIF-1: a blocked run tells the org's HR users in-app; `test_the_in_app_consumer_*` covers its
    # other event (a decided change request) with an org-B requester id.
    IN_APP_CONSUMER: ConsumerCase(
        RUN_BLOCKED, lambda org: {"run_id": f"run_xt_{org}", "task_id": "xt_task"}, in_app_handler
    ),
    # RUN-1: the orchestrator executes the org's seeded run (`XT_RUNS`): it reads and writes
    # `onboarding_runs`, `run_tasks` and its checkpoints, all in the job's org.
    START_RUN: ConsumerCase(RUN_STARTED, lambda org: {"run_id": XT_RUNS[org][0]}, _start_run),
    # RUN-3: a sink refused a grant; the consumer fails that org's running task (the seeded runs are
    # only `started`, so it finds none to fail: the cross-tenant check is that it touches nothing of org B).
    FAIL_TASK: ConsumerCase(
        SINK_REJECTED,
        lambda org: {
            "run_id": XT_RUNS[org][0],
            "agent": "provisioning_agent",
            "sink": "notify.telegram",
            "reason": "wrong_run",
        },
        _fail_task,
    ),
    # RUN-2: the same handler drives a resumed run, and the cleanup consumer deletes a cancelled run's
    # checkpoints; both read the org's seeded run (`XT_RUNS`) in the job's org only.
    RESUME_RUN: ConsumerCase(RUN_RESUMED, lambda org: {"run_id": XT_RUNS[org][0]}, _start_run),
    CLEANUP_RUN: ConsumerCase(RUN_CANCELLED, lambda org: {"run_id": XT_RUNS[org][0]}, cleanup_run_handler),
    # HIL-1: an approval was decided, a Telegram button was pressed, an approval was requested: each handler
    # reads the approval of its job's org (org A's is pending, so none acts) and writes nothing of org B's.
    RESUME_APPROVAL: ConsumerCase(
        APPROVAL_DECIDED, lambda org: {"approval_id": XT_APPROVALS[org], "run_id": XT_RUNS[org][0]}, _resume_run
    ),
    RECORD_DECISION_CONSUMER: ConsumerCase(
        DECISION_RECEIVED,
        lambda org: {
            "ref_type": "approval",
            "ref_id": XT_APPROVALS[org],
            "decision": "approve",
            "channel": "telegram",
            "outbox_id": f"out_xt_{org}",
            "callback_query_id": f"cq_xt_{org}",
        },
        _record_decision,
    ),
    APPROVAL_NOTIFY_CONSUMER: ConsumerCase(
        APPROVAL_REQUESTED,
        lambda org: {"approval_id": XT_APPROVALS[org], "run_id": XT_RUNS[org][0], "task_id": "manager_approval"},
        _approval_notify,
    ),
    # HIL-2: an approval is overdue; the escalation reads the job's org's approval and HR users only (org A's
    # approval is pending, so org A's HR are told) and writes notes and messages in that org alone.
    APPROVAL_SLA_CONSUMER: ConsumerCase(
        APPROVAL_OVERDUE,
        lambda org: {
            "approval_id": XT_APPROVALS[org],
            "run_id": XT_RUNS[org][0],
            "task_id": "manager_approval",
            "stage": "escalation",
            "escalate_to": "HR",
        },
        _approval_sla,
    ),
}


def _probe(store: DataStore) -> Handler:
    async def handle(job: JobContext) -> None:
        seen = await store.tenant("new_hires").count({})
        await store.tenant("xt_probe").insert_one({"job_id": job.job_id, "hires_seen": seen})

    return handle


PROBE = "xt.probe"
PROBE_CASE = ConsumerCase("xt.probed", lambda org: {"run_id": f"run_xt_{org}"}, _probe)


def _observing(handler: Handler, observed: Observed) -> Handler:
    async def handle(job: JobContext) -> None:
        observed.append((current_tenant().org_id, job.org_id))
        await handler(job)

    return handle


async def _publish(bus: MongoEventBus, case: ConsumerCase, org: str, key: str) -> tuple[str, ...]:
    with tenant_scope(org):
        published = await bus.publish(case.event_type, case.payload(org), idempotency_key=key)
    assert not published.duplicate, f"{org}: idempotency key {key!r} collided with another org's event"
    return published.job_ids


async def _drain(bus: MongoEventBus, store: DataStore, consumer: str, case: ConsumerCase) -> Observed:
    observed: Observed = []
    worker = bus.worker({consumer: _observing(case.handler(store), observed)})
    while await worker.run_once() is not None:
        pass
    return observed


def assert_consumer_stays_in_its_org(xt: World, unscoped: Unscoped, consumer: str, case: ConsumerCase) -> None:
    subscriptions = {case.event_type: [consumer]}

    async def one_org_a_event() -> Observed:
        store = xt.store()
        try:
            bus = MongoEventBus(store, subscriptions=subscriptions)
            await bus.ensure_indexes()
            await _publish(bus, case, ORG_A, f"xt-{consumer}")
            return await _drain(bus, store, consumer, case)
        finally:
            await store.close()

    async def interleaved() -> Observed:
        store = xt.store()
        try:
            bus = MongoEventBus(store, subscriptions=subscriptions)
            await _publish(bus, case, ORG_B, f"xt-{consumer}")  # same key as org A's first event
            await _publish(bus, case, ORG_A, f"xt-{consumer}-2")
            await _publish(bus, case, ORG_B, f"xt-{consumer}-2")
            return await _drain(bus, store, consumer, case)
        finally:
            await store.close()

    before = xt.org_b_snapshot(unscoped)
    assert asyncio.run(one_org_a_event()) == [(ORG_A, ORG_A)]
    assert xt.org_b_snapshot(unscoped) == before, f"{consumer}: handling an org-A event changed org B"
    mixed = asyncio.run(interleaved())
    assert all(tenant == job_org for tenant, job_org in mixed), f"{consumer}: a handler ran in another org: {mixed}"
    assert sorted(job_org for _, job_org in mixed) == [ORG_A, ORG_B, ORG_B]


@pytest.mark.parametrize("consumer", [*CONSUMER_CASES, PROBE])
def test_a_consumer_handling_an_org_a_event_stays_in_org_a(xt_world: World, unscoped: Unscoped, consumer: str) -> None:
    case = CONSUMER_CASES.get(consumer, PROBE_CASE)
    assert_consumer_stays_in_its_org(xt_world, unscoped, consumer, case)
    if consumer == PROBE:
        # What the probe read and wrote: each org's own hire count, stored in that org only.
        with unscoped("root read-back of the probe's writes in every org"):
            written = sorted((d["org_id"], d["hires_seen"]) for d in xt_world.root_db["xt_probe"].find())
        own = {org: len(tenant_documents(org)["new_hires"]) for org in (ORG_A, ORG_B)}
        assert written == sorted([(ORG_A, own[ORG_A]), (ORG_A, own[ORG_A]), (ORG_B, own[ORG_B]), (ORG_B, own[ORG_B])])


def test_the_in_app_consumer_never_tells_another_orgs_user(xt_world: World, unscoped: Unscoped) -> None:
    """NOTIF-1: an org-A change request naming an org-B user as its requester notifies nobody, and a
    blocked org-A run notifies org A's HR users only."""
    subscriptions = NOTIFY_SUBSCRIPTIONS

    async def main() -> None:
        store = xt_world.store()
        try:
            bus = MongoEventBus(store, subscriptions=subscriptions)
            await bus.ensure_indexes()
            with tenant_scope(ORG_A):
                await bus.publish(
                    CHANGE_REQUEST_DECIDED,
                    {"change_request_id": "cr_xt_1", "requested_by": "u_globex_hr1", "decision": "accepted"},
                    idempotency_key="xt-cr-1",
                )
                await bus.publish(
                    RUN_BLOCKED, {"run_id": "run_acme_001", "task_id": "xt_task"}, idempotency_key="xt-rb-1"
                )
            worker = bus.worker({IN_APP_CONSUMER: in_app_handler(store)})
            while await worker.run_once() is not None:
                pass
        finally:
            await store.close()

    before = xt_world.org_b_snapshot(unscoped)
    asyncio.run(main())
    assert xt_world.org_b_snapshot(unscoped) == before
    with unscoped("root read-back of every in-app notification, in every org"):
        stored = sorted((d["org_id"], d["user_id"], d["kind"]) for d in xt_world.root_db["notifications"].find())
    hr_users = sorted(u["_id"] for u in seed_users() if u["org_id"] == ORG_A and u["role"] in ("HR", "HR_ADMIN"))
    assert stored == [(ORG_A, user, RUN_BLOCKED) for user in hr_users]


# --- orchestrator.start_run (RUN-1): its own cross-tenant checks --------------------------------------


def _drain_start_run(xt: World, org: str, run_id: str, key: str) -> None:
    async def go() -> None:
        store = xt.store()
        try:
            bus = MongoEventBus(store, subscriptions={RUN_STARTED: [START_RUN]})
            await bus.ensure_indexes()
            with tenant_scope(org):
                await bus.publish(RUN_STARTED, {"run_id": run_id}, idempotency_key=key, run_id=run_id)
            worker = bus.worker({START_RUN: _start_run(store)})
            while await worker.run_once() is not None:
                pass
        finally:
            await store.close()

    asyncio.run(go())


def _decide_and_resume(xt: World, org: str, run_id: str) -> None:
    """HIL-1: the run paused at its approval task. Decide the approval as its approver (the real write: the
    decision, its audit record and `approval.decided`, in one transaction) and let the real `resume_run`
    consumer resume the checkpointed graph."""

    async def go() -> None:
        store = xt.store()
        try:
            bus = MongoEventBus(store, subscriptions={APPROVAL_DECIDED: [RESUME_APPROVAL]})
            await bus.ensure_indexes()
            approvals = ApprovalStore(store)
            with tenant_scope(org):
                [approval] = await approvals.for_task(run_id, "manager_approval")
                decider = ApprovalDecider(approvals, bus, auditor=AuditLog(store))
                await decider.decide(
                    approval["_id"],
                    decision=Decision.APPROVE,
                    comment=None,
                    channel="web",
                    user_id=approval["approver_id"] or "u_xt_hr",
                    role="HR",
                )
            worker = bus.worker({RESUME_APPROVAL: resume_approval_handler(store, StubExecutor())})
            while await worker.run_once() is not None:
                pass
        finally:
            await store.close()

    asyncio.run(go())


def test_an_org_a_job_naming_org_bs_run_changes_nothing_and_starts_nothing(xt_world: World, unscoped: Unscoped) -> None:
    """A confused deputy: org A's job carries org B's run id. The consumer reads in org A only, so it
    finds no run, writes no checkpoint for that thread, and org B's run stays as it was."""
    b_run = XT_RUNS[ORG_B][0]
    before = xt_world.org_b_snapshot(unscoped)
    _drain_start_run(xt_world, ORG_A, b_run, "xt-foreign-run")
    assert xt_world.org_b_snapshot(unscoped) == before, "an org-A job changed org B"
    with unscoped("root read-back: no checkpoint or run was made for org B's run id"):
        db = xt_world.root_db
        assert db["run_checkpoints"].count_documents({"thread_id": {"$regex": b_run}}) == 0
        assert db["onboarding_runs"].count_documents({"_id": b_run, "org_id": ORG_A}) == 0
        b_record = db["onboarding_runs"].find_one({"_id": b_run})
    assert b_record is not None
    assert b_record["status"] == "started"


def test_the_orchestrator_completes_an_org_a_run_entirely_in_org_a(xt_world: World, unscoped: Unscoped) -> None:
    a_run = XT_RUNS[ORG_A][0]
    before = xt_world.org_b_snapshot(unscoped)
    _drain_start_run(xt_world, ORG_A, a_run, "xt-own-run")
    with unscoped("root read-back: the seed template's approval task paused the run (HIL-1)"):
        paused = xt_world.root_db["onboarding_runs"].find_one({"_id": a_run})
    assert paused is not None
    assert paused["status"] == "running"
    _decide_and_resume(xt_world, ORG_A, a_run)
    assert xt_world.org_b_snapshot(unscoped) == before
    with unscoped("root read-back of org A's executed run, its tasks and checkpoints"):
        db = xt_world.root_db
        run = db["onboarding_runs"].find_one({"_id": a_run})
        tasks = list(db["run_tasks"].find({"run_id": a_run}))
        checkpoints = list(db["run_checkpoints"].find({"thread_id": f"{ORG_A}:{a_run}"}))
        foreign_threads = db["run_checkpoints"].count_documents({"thread_id": {"$not": {"$regex": f"^{ORG_A}:"}}})
    assert run is not None
    assert (run["org_id"], run["status"]) == (ORG_A, "completed")
    assert {t["status"] for t in tasks} <= {"done", "skipped"}
    assert {t["org_id"] for t in tasks} == {ORG_A}
    # RUN-2: a completed run keeps no checkpoint (they are deleted with it), and no checkpoint of any
    # other thread than an org-A one exists, while it ran or after.
    assert checkpoints == []
    assert foreign_threads == 0


# --- orchestrator.fail_task (RUN-3): its own cross-tenant check ---------------------------------------


def test_an_org_a_rejection_naming_org_bs_running_task_fails_nothing_in_org_b(
    xt_world: World, unscoped: Unscoped
) -> None:
    """A confused deputy: org A's `task.sink_rejected` carries org B's run and task ids while that task is
    `running`. The consumer reads in org A only, so org B's task keeps running and nothing of B changes."""
    b_run = XT_RUNS[ORG_B][0]
    with unscoped("root setup: org B's first task is running, as if its agent were mid-task"):
        db = xt_world.root_db
        first = db["run_tasks"].find_one({"run_id": b_run}, sort=[("position", 1)])
        assert first is not None
        db["run_tasks"].update_one({"_id": first["_id"]}, {"$set": {"status": "running"}})

    async def go() -> None:
        store = xt_world.store()
        try:
            bus = MongoEventBus(store, subscriptions={SINK_REJECTED: [FAIL_TASK]})
            await bus.ensure_indexes()
            payload = {"run_id": b_run, "task_id": first["task_id"], "agent": "x", "sink": "s", "reason": "wrong_run"}
            with tenant_scope(ORG_A):
                await bus.publish(SINK_REJECTED, payload, idempotency_key="xt-foreign-reject", run_id=b_run)
            worker = bus.worker({FAIL_TASK: fail_task_handler(store)})
            while await worker.run_once() is not None:
                pass
        finally:
            await store.close()

    before = xt_world.org_b_snapshot(unscoped)
    asyncio.run(go())
    assert xt_world.org_b_snapshot(unscoped) == before, "an org-A rejection changed org B"
    with unscoped("root read-back of org B's task"):
        after = xt_world.root_db["run_tasks"].find_one({"_id": first["_id"]})
    assert after is not None
    assert after["status"] == "running"
    assert "failure" not in after
