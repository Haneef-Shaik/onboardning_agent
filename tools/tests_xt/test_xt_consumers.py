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
from onb_agents.runs import RUN_STARTED, START_RUN
from onb_contracts.notify import CHANGE_REQUEST_DECIDED, IN_APP_CONSUMER, NOTIFY_SUBSCRIPTIONS, RUN_BLOCKED
from onb_data import DataStore, current_tenant, tenant_scope
from onb_eventbus import Handler, JobContext, MongoEventBus
from onb_mcp_mocks.notify.in_app import in_app_handler
from onb_orchestrator.executor import StubExecutor
from onb_orchestrator.runner import start_run_handler

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
