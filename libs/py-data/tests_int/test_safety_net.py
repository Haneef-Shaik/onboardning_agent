"""The runtime safety net against the compose Mongo (T-2 boxes 1 and 2; PRD §7.9 Safety net).

`conftest.py` installs one `TenantCommandListener` globally when the test session starts, before any
client exists, and its autouse `tenant_safety_net_guard` fixture fails any test that leaves an
unscoped command behind. These tests prove the net is on and that it trips.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data import UnscopedCommandError, connect, tenant_scope
from onb_data.safety_net import TenantCommandListener
from pymongo import AsyncMongoClient, MongoClient, monitoring

Client = MongoClient[dict[str, object]]
GUARD_FIXTURE = "tenant_safety_net_guard"
COLLECTION = "hires"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[str]:
    name = f"it_t2_{uuid.uuid4().hex[:12]}"
    root_client[name][COLLECTION].insert_many(
        [{"_id": "a1", "org_id": "org_alpha", "name": "Asha"}, {"_id": "b1", "org_id": "org_beta", "name": "Bina"}]
    )
    yield name
    root_client.drop_database(name)


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


# --- box 2: the listener is on for every integration test ---------------------------------------


def test_every_integration_test_runs_under_the_safety_net_guard(request: pytest.FixtureRequest) -> None:
    items = request.session.items
    assert items, "the session collected integration tests"
    unguarded = [item.nodeid for item in items if GUARD_FIXTURE not in getattr(item, "fixturenames", ())]
    assert unguarded == []


def test_the_listener_is_registered_globally_before_any_client(tenant_safety_net: TenantCommandListener) -> None:
    assert isinstance(tenant_safety_net, TenantCommandListener)
    assert monitoring._LISTENERS.command_listeners.count(tenant_safety_net) == 1


def test_a_client_made_inside_a_test_reports_to_the_listener(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: str
) -> None:
    """Neither client is given a listener: they get it from the global registry."""
    fresh: Client = MongoClient(_uri(root_client), serverSelectionTimeoutMS=10_000)
    try:
        fresh[scratch][COLLECTION].count_documents({})
    finally:
        fresh.close()
    drained = tenant_safety_net.drain()
    assert [(v.database, v.collection, v.command) for v in drained] == [(scratch, COLLECTION, "aggregate")]


def test_scoped_repository_traffic_is_not_flagged(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: str
) -> None:
    async def main() -> None:
        store = connect(_uri(root_client), scratch)
        try:
            repo = store.tenant(COLLECTION)
            with tenant_scope("org_alpha"):
                await repo.find()
                await repo.count()
                await repo.insert_one({"name": "Anil"})
                await repo.update_many({}, {"$set": {"seen": True}})
                await repo.aggregate([{"$sort": {"name": 1}}])
                await repo.delete_one({"name": "Anil"})
            await store.ensure_index(COLLECTION, [("org_id", 1), ("name", 1)])
        finally:
            await store.close()

    asyncio.run(main())
    assert tenant_safety_net.violations == ()


# --- box 1: a raw unscoped find through a deliberately leaked client trips the listener ---------


def test_an_unscoped_find_through_a_leaked_client_trips_the_listener(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: str
) -> None:
    async def main() -> list[dict[str, Any]]:
        store = connect(_uri(root_client), scratch)
        leaked: AsyncMongoClient[dict[str, Any]] | None = store._client  # the deliberate leak
        assert leaked is not None
        try:
            return await leaked[scratch][COLLECTION].find({}).to_list()
        finally:
            await store.close()

    rows = asyncio.run(main())
    assert {row["org_id"] for row in rows} == {"org_alpha", "org_beta"}, "the raw find really crossed orgs"
    with pytest.raises(UnscopedCommandError, match=rf"unscoped find on {scratch}\.{COLLECTION}"):
        tenant_safety_net.check()


def test_an_unscoped_find_through_a_raw_client_trips_the_listener(
    tenant_safety_net: TenantCommandListener, root_client: Client, scratch: str
) -> None:
    raw: Client = MongoClient(_uri(root_client), serverSelectionTimeoutMS=10_000)
    try:
        raw[scratch][COLLECTION].find_one({"_id": "b1"})
    finally:
        raw.close()
    drained = tenant_safety_net.drain()
    assert [(v.collection, v.command) for v in drained] == [(COLLECTION, "find")]
    assert "b1" not in drained[0].describe(), "a violation never repeats values from the command"
