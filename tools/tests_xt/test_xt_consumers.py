"""Every event consumer: handling an org-A event never reads, changes or reveals org B (PRD §7.9).

`CONSUMER_CASES` has one row per consumer in `surface.PLATFORM_SUBSCRIPTIONS`; `test_xt_coverage.py`
fails the run when a consumer has no row. The platform subscribes no consumer yet (the orchestrator
does, from RUN-1 on), so the generic check also runs on `xt.probe`, a consumer of this suite that
reads and writes through the store like a real one. That keeps the check itself exercised until the
first real consumer arrives.

The generic check publishes one org-A event, runs a worker over the real handler and asserts:
* the handler ran inside org A's tenant context, and its `JobContext` says org A;
* org B's documents are unchanged;
* the same idempotency key in org B is a new event, not a duplicate (no inference through the
  per-org unique index).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from onb_data import DataStore, current_tenant, tenant_scope
from onb_eventbus import Handler, JobContext, MongoEventBus

from .conftest import Unscoped, World
from .test_xt_mcp import _org_b_snapshot
from .world import ORG_A, ORG_B, tenant_documents


@dataclass(frozen=True)
class ConsumerCase:
    event_type: str
    """An event type the consumer is subscribed to."""
    payload: Callable[[str], Mapping[str, Any]]
    """An ordinary payload for an org (ids and `ptk_` tokens only)."""
    handler: Callable[[DataStore], Handler]
    """The production handler, built over the store."""


# One row per platform consumer (RUN-1 adds the orchestrator's). Empty while nothing subscribes.
CONSUMER_CASES: Mapping[str, ConsumerCase] = {}


def _probe(store: DataStore) -> Handler:
    async def handle(job: JobContext) -> None:
        seen = await store.tenant("new_hires").count({})
        await store.tenant("xt_probe").insert_one({"job_id": job.job_id, "hires_seen": seen})

    return handle


PROBE = "xt.probe"
PROBE_CASE = ConsumerCase("xt.probed", lambda org: {"run_id": f"run_xt_{org}"}, _probe)


@dataclass
class _Observed:
    tenants: list[str]
    job_orgs: list[str]


def _observing(handler: Handler, observed: _Observed) -> Handler:
    async def handle(job: JobContext) -> None:
        observed.tenants.append(current_tenant().org_id)
        observed.job_orgs.append(job.org_id)
        await handler(job)

    return handle


async def _publish_and_handle(xt: World, consumer: str, case: ConsumerCase, observed: _Observed) -> None:
    store = xt.store()
    try:
        bus = MongoEventBus(store, subscriptions={case.event_type: [consumer]})
        await bus.ensure_indexes()
        with tenant_scope(ORG_A):
            published = await bus.publish(case.event_type, case.payload(ORG_A), idempotency_key=f"xt-{consumer}")
        assert not published.duplicate
        worker = bus.worker({consumer: _observing(case.handler(store), observed)})
        processed = await worker.run_once()
        assert processed is not None
        assert processed.job_id in published.job_ids
        assert await worker.run_once() is None
    finally:
        await store.close()


async def _publish_same_key_in_org_b(xt: World, consumer: str, case: ConsumerCase) -> bool:
    store = xt.store()
    try:
        bus = MongoEventBus(store, subscriptions={case.event_type: [consumer]})
        with tenant_scope(ORG_B):
            published = await bus.publish(case.event_type, case.payload(ORG_B), idempotency_key=f"xt-{consumer}")
        return published.duplicate
    finally:
        await store.close()


def assert_consumer_stays_in_its_org(xt: World, unscoped: Unscoped, consumer: str, case: ConsumerCase) -> None:
    before = _org_b_snapshot(xt, unscoped)
    observed = _Observed([], [])
    asyncio.run(_publish_and_handle(xt, consumer, case, observed))
    assert observed.tenants == [ORG_A]
    assert observed.job_orgs == [ORG_A]
    assert _org_b_snapshot(xt, unscoped) == before, f"{consumer}: handling an org-A event changed org B"
    assert asyncio.run(_publish_same_key_in_org_b(xt, consumer, case)) is False, "the idempotency key leaks"


_CASES = {**CONSUMER_CASES, PROBE: PROBE_CASE}


@pytest.mark.parametrize("consumer", list(_CASES))
def test_a_consumer_handling_an_org_a_event_stays_in_org_a(xt_world: World, unscoped: Unscoped, consumer: str) -> None:
    assert_consumer_stays_in_its_org(xt_world, unscoped, consumer, _CASES[consumer])


def test_the_probe_consumer_saw_only_org_a(xt_world: World, unscoped: Unscoped) -> None:
    """What the probe read and wrote: org A's hire count, stored in org A only."""
    assert_consumer_stays_in_its_org(xt_world, unscoped, PROBE, PROBE_CASE)
    with unscoped("root read-back of the probe's writes in every org"):
        written = [(d["org_id"], d["hires_seen"]) for d in xt_world.root_db["xt_probe"].find()]
    assert written == [(ORG_A, len(tenant_documents(ORG_A)["new_hires"]))]
