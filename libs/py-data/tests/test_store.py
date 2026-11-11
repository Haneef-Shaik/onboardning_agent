"""DataStore, the global-collection list, the index helper and GlobalRepository (PRD §7.9 Indexes)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from onb_data import (
    GLOBAL_COLLECTIONS,
    GlobalRepository,
    InvalidIndexError,
    NotFound,
    TenantRepository,
    TenantViolationError,
    UnknownCollectionError,
    connect,
    tenant_scope,
)
from onb_data import store as store_module

from .fakes import FakeClient, FakeCollection, FakeDatabase, make_store


def test_the_global_collections_are_exactly_the_short_list() -> None:
    # user_logins: the login directory (AUTH-1, ADR 0004); it holds only {_id: email hash, org_id, user_id}.
    assert frozenset({"orgs", "agent_registry", "tool_catalog", "user_logins"}) == GLOBAL_COLLECTIONS


def test_users_stay_a_tenant_collection() -> None:
    assert "users" not in GLOBAL_COLLECTIONS
    assert isinstance(make_store().tenant("users"), TenantRepository)


def test_tenant_collections_get_a_tenant_repository() -> None:
    repo = make_store().tenant("hires")
    assert isinstance(repo, TenantRepository)
    assert repo.collection_name == "hires"


@pytest.mark.parametrize("name", sorted(GLOBAL_COLLECTIONS))
def test_a_global_collection_is_never_handed_out_as_a_tenant_one(name: str) -> None:
    with pytest.raises(UnknownCollectionError, match=name):
        make_store().tenant(name)


@pytest.mark.parametrize("name", sorted(GLOBAL_COLLECTIONS))
def test_global_collections_get_a_global_repository(name: str) -> None:
    repo = make_store().shared(name)
    assert isinstance(repo, GlobalRepository)
    assert repo.collection_name == name


def test_a_tenant_collection_is_never_handed_out_unscoped() -> None:
    with pytest.raises(UnknownCollectionError, match="hires"):
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
    with pytest.raises(InvalidIndexError, match="org_id"):
        _ensure(db, "hires", [("email_hash", 1), ("org_id", 1)], unique=True)
    assert db["hires"].calls == []


def test_a_tenant_index_with_an_org_id_prefix_is_created() -> None:
    db = FakeDatabase()
    name = _ensure(db, "hires", [("org_id", 1), ("email_hash", 1)], unique=True, name="org_email")
    assert name == "org_email"
    call = db["hires"].last("create_index")
    assert call.args == ([("org_id", 1), ("email_hash", 1)],)
    assert call.kwargs == {"unique": True, "name": "org_email"}


def test_a_partial_unique_index_passes_its_filter_to_the_driver() -> None:
    # HIRE-1: `{org_id, email_hash}` is unique only among hires that have an email_hash.
    db = FakeDatabase()
    _ensure(db, "hires", [("org_id", 1), ("email_hash", 1)], unique=True, partial={"email_hash": {"$exists": True}})
    assert db["hires"].last("create_index").kwargs == {
        "unique": True,
        "partialFilterExpression": {"email_hash": {"$exists": True}},
    }


@pytest.mark.parametrize(
    "partial",
    [{}, {"org_id": {"$exists": True}}, {"$or": [{"a": 1}]}, {"email_hash": 1}, {"email_hash": {"$exists": False}}],
    ids=["empty", "org_id", "operator", "equality", "exists-false"],
)
def test_a_partial_filter_is_field_exists_only(partial: Any) -> None:
    # Only `{field: {"$exists": True}}` on non-org fields: a filter can never narrow an index by org.
    db = FakeDatabase()
    with pytest.raises(InvalidIndexError, match="partial"):
        _ensure(db, "hires", [("org_id", 1), ("email_hash", 1)], unique=True, partial=partial)
    assert db["hires"].calls == []


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
    with pytest.raises(InvalidIndexError):
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
    with pytest.raises(ValueError, match=r"uri|database"):
        connect(uri, database)


def test_connect_checks_the_database_name_before_making_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[object] = []
    monkeypatch.setattr(store_module, "_AsyncMongoClient", lambda *a, **kw: made.append(kw))
    with pytest.raises(ValueError, match="database"):
        connect("mongodb://x", "a.b")
    assert made == []


def test_connect_passes_event_listeners_to_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-2 attaches its command listener here."""
    made: list[dict[str, Any]] = []

    def fake_client(uri: str, **kwargs: Any) -> dict[str, FakeDatabase]:
        made.append({"uri": uri, **kwargs})
        return {"onb": FakeDatabase()}

    listener = object()
    monkeypatch.setattr(store_module, "_AsyncMongoClient", fake_client)
    connect("mongodb://x", "onb", event_listeners=[listener])
    assert made[0]["event_listeners"] == [listener]
    assert made[0]["uri"] == "mongodb://x"


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
    with pytest.raises(TypeError, match="document"):
        asyncio.run(repo.insert_one(["not", "a", "document"]))  # type: ignore[arg-type]


def test_a_global_lookup_of_a_missing_id_raises_not_found() -> None:
    with pytest.raises(NotFound):
        asyncio.run(_global(FakeCollection(name="orgs")).get("nope"))


def test_global_pipelines_still_refuse_cross_collection_stages() -> None:
    with tenant_scope("org_a"), pytest.raises(TenantViolationError):
        asyncio.run(_global(FakeCollection(name="orgs")).aggregate([{"$out": "hires"}]))
