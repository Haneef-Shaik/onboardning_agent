"""Every index on a tenant collection starts with `org_id` (T-2 box 3; PRD §7.9 Indexes).

`test_every_tenant_index_on_the_server_starts_with_org_id` walks every database and collection on
the compose Mongo during `make test-int`, so an index created by any path (a migration, a manual
`createIndex`, a future goal's bootstrap) fails the gate if it is not led by `org_id`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from onb_data.safety_net import TenantCommandListener, find_index_violation, is_tenant_collection
from pymongo import MongoClient

Client = MongoClient[dict[str, object]]
SYSTEM_DATABASES = frozenset({"admin", "config", "local"})


def tenant_index_violations(client: Client, databases: list[str] | None = None) -> list[str]:
    """`<db>.<collection> index <name>: <reason>` for each tenant index not led by `org_id`."""
    found: list[str] = []
    names = databases if databases is not None else client.list_database_names()
    for db_name in sorted(set(names) - SYSTEM_DATABASES):
        database = client[db_name]
        for info in database.list_collections(filter={"type": {"$in": ["collection", "timeseries"]}}):
            collection = str(info["name"])
            if not is_tenant_collection(db_name, collection):
                continue
            for index in database[collection].list_indexes():
                reason = find_index_violation(collection, index["key"])
                if reason:
                    found.append(f"{db_name}.{collection} index {index['name']}: {reason}")
    return found


@pytest.fixture
def scratch_name(root_client: Client) -> Iterator[str]:
    name = f"it_t2_idx_{uuid.uuid4().hex[:12]}"
    yield name
    root_client.drop_database(name)


def test_every_tenant_index_on_the_server_starts_with_org_id(root_client: Client) -> None:
    assert tenant_index_violations(root_client) == []


def test_the_index_check_catches_a_tenant_index_not_led_by_org_id(
    root_client: Client, scratch_name: str, tenant_safety_net: TenantCommandListener
) -> None:
    hires = root_client[scratch_name]["hires"]
    hires.create_index([("org_id", 1), ("email_hash", 1)], name="org_email", unique=True)
    hires.create_index([("email_hash", 1)], name="email_only")
    root_client[scratch_name]["orgs"].create_index([("slug", 1)], name="slug")

    assert tenant_index_violations(root_client, [scratch_name]) == [
        f"{scratch_name}.hires index email_only: index on tenant collection hires does not start with org_id"
    ]
    # The listener saw the bad createIndexes too, as it happened.
    drained = tenant_safety_net.drain()
    assert [(v.collection, v.command) for v in drained] == [("hires", "createIndexes")]


def test_the_index_check_ignores_views_and_system_databases(root_client: Client, scratch_name: str) -> None:
    database = root_client[scratch_name]
    database["hires"].insert_one({"org_id": "org_alpha"})
    database.create_collection("hires_view", viewOn="hires", pipeline=[{"$match": {"org_id": "org_alpha"}}])
    assert tenant_index_violations(root_client, [scratch_name, "admin"]) == []
