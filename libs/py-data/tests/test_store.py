"""DataStore, the global-collection list, the index helper and GlobalRepository (PRD §7.9 Indexes)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onb_data import (
    GLOBAL_COLLECTIONS,
    GlobalRepository,
    InvalidIndex,
    NotFound,
    TenantRepository,
    TenantViolation,
    UnknownCollection,
    connect,
    tenant_scope,
)

from .fakes import FakeClient, FakeCollection, FakeDatabase, make_store


def test_the_global_collections_are_exactly_the_short_list() -> None:
    assert frozenset({"orgs", "agent_registry", "tool_catalog"}) == GLOBAL_COLLECTIONS


def test_tenant_collections_get_a_tenant_repository() -> None:
    repo = make_store().tenant("hires")
    assert isinstance(repo, TenantRepository)
    assert repo.collection_name == "hires"


@pytest.mark.parametrize("name", sorted(GLOBAL_COLLECTIONS))
def test_a_global_collection_is_never_handed_out_as_a_tenant_one(name: str) -> None:
    with pytest.raises(UnknownCollection, match=name):
        make_store().tenant(name)


@pytest.mark.parametrize("name", sorted(GLOBAL_COLLECTIONS))
def test_global_collections_get_a_global_repository(name: str) -> None:
    repo = make_store().shared(name)
    assert isinstance(repo, GlobalRepository)
    assert repo.collection_name == name


def test_a_tenant_collection_is_never_handed_out_unscoped() -> None:
    with pytest.raises(UnknownCollection, match="hires"):
        make_store().shared("hires")


@pytest.mark.parametrize("name", ["", "has$dollar", "system.users", "a\x00b", 7])
def test_invalid_collection_names_are_refused(name: Any) -> None:
    with pytest.raises(ValueError, match="collection"):
        make_store().tenant(name)


# --- indexes -------------------------------------------------------------------------------


def _ensure(db: FakeDatabase, collection: str, keys: Any, **kwargs: Any) -> str:
    return asyncio.run(make_store(db).ensure_index(collection, keys, **kwargs))


def test_a_tenant_index_must_start_with_org_id() -> None:
    db = FakeDatabase()
    with pytest.raises(InvalidIndex, match="org_id"):
        _ensure(db, "hires", [("email_hash", 1), ("org_id", 1)], unique=True)
    assert db["hires"].calls == []


def test_a_tenant_index_with_an_org_id_prefix_is_created() -> None:
    db = FakeDatabase()
    name = _ensure(db, "hires", [("org_id", 1), ("email_hash", 1)], unique=True, name="org_email")
    assert name == "org_email"
    call = db["hires"].last("create_index")
    assert call.args == ([("org_id", 1), ("email_hash", 1)],)
    assert call.kwargs == {"unique": True, "name": "org_email"}


def test_an_index_on_org_id_alone_is_allowed_and_named_by_the_driver() -> None:
    db = FakeDatabase()
    assert _ensure(db, "hires", [("org_id", -1)]) == "org_id_-1"
    assert db["hires"].last("create_index").kwargs == {"unique": False}


def test_a_global_index_needs_no_org_id() -> None:
    db = FakeDatabase()
    _ensure(db, "orgs", [("slug", 1)], unique=True)
    assert db["orgs"].last("create_index").args == ([("slug", 1)],)


@pytest.mark.parametrize(
    "keys",
    [[], "org_id", [("org_id", 2)], [("org_id",)], [("", 1)], [("org_id", 1), ("org_id", 1)], [(1, 1)]],
    ids=str,
)
def test_malformed_index_keys_are_refused(keys: Any) -> None:
    with pytest.raises(InvalidIndex):
        _ensure(FakeDatabase(), "hires", keys)


def test_text_and_hashed_index_directions_are_accepted_after_the_org_prefix() -> None:
    db = FakeDatabase()
    _ensure(db, "hires", [("org_id", 1), ("notes", "text")])
    _ensure(db, "hires", [("org_id", "hashed")])
    assert db["hires"].last("create_index").args == ([("org_id", "hashed")],)


# --- lifecycle -----------------------------------------------------------------------------


def test_ping_and_close_go_to_the_driver() -> None:
    db, client = FakeDatabase(), FakeClient()
    store = make_store(db, client)
    asyncio.run(store.ping())
    asyncio.run(store.close())
    assert db.commands == ["ping"]
    assert client.closed


def test_closing_a_store_without_a_client_is_a_no_op() -> None:
    asyncio.run(make_store().close())


def test_connect_builds_a_lazy_store_without_touching_the_network() -> None:
    async def main() -> str:
        store = connect("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=1", "onb_test")
        try:
            return store.tenant("hires").collection_name
        finally:
            await store.close()

    assert asyncio.run(main()) == "hires"


@pytest.mark.parametrize(("uri", "database"), [("", "db"), ("mongodb://x", ""), ("mongodb://x", "a.b")])
def test_connect_refuses_a_missing_uri_or_bad_database_name(uri: str, database: str) -> None:
    with pytest.raises(ValueError, match="uri|database"):
        connect(uri, database)


# --- GlobalRepository ----------------------------------------------------------------------


def _global(collection: FakeCollection) -> GlobalRepository:
    return make_store(FakeDatabase({collection.name: collection})).shared(collection.name)


def test_global_reads_need_no_tenant_context_and_add_no_org_id() -> None:
    collection = FakeCollection(name="orgs", docs=[{"_id": "org_a"}], find_one_result={"_id": "org_a"}, count_result=1)
    repo = _global(collection)
    assert asyncio.run(repo.find({"slug": "acme"})) == [{"_id": "org_a"}]
    assert asyncio.run(repo.get("org_a")) == {"_id": "org_a"}
    assert asyncio.run(repo.count()) == 1
    assert asyncio.run(repo.aggregate([{"$sort": {"_id": 1}}])) == [{"_id": "org_a"}]
    assert collection.last("find").args[0] == {"slug": "acme"}
    assert collection.last("find_one").args[0] == {"_id": "org_a"}
    assert collection.last("aggregate").args[0] == [{"$sort": {"_id": 1}}]


def test_global_writes_add_no_org_id() -> None:
    collection = FakeCollection(name="tool_catalog")
    repo = _global(collection)
    asyncio.run(repo.insert_one({"_id": "t1"}))
    asyncio.run(repo.update_one({"_id": "t1"}, {"$set": {"v": 2}}, upsert=True))
    assert collection.last("insert_one").args[0] == {"_id": "t1"}
    assert collection.last("update_one").args[1] == {"$set": {"v": 2}}


def test_a_global_lookup_of_a_missing_id_raises_not_found() -> None:
    with pytest.raises(NotFound):
        asyncio.run(_global(FakeCollection(name="orgs")).get("nope"))


def test_global_pipelines_still_refuse_cross_collection_stages() -> None:
    with tenant_scope("org_a"), pytest.raises(TenantViolation):
        asyncio.run(_global(FakeCollection(name="orgs")).aggregate([{"$out": "hires"}]))
