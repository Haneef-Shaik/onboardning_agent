"""TenantRepository unit tests (PRD §7.9 Data access, IDs; T-1 boxes 1 and 2).

Each test checks what reaches the driver. The two-org behaviour against a real MongoDB is in
`tests_int/test_tenant_repository.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import pytest
from onb_data import NoTenantContextError, NotFound, NotFoundError, TenantRepository, TenantViolationError, tenant_scope

from .fakes import FakeCollection, FakeDatabase, make_store

ORG = "org_a"
OTHER = "org_b"

Op = Callable[[TenantRepository], Awaitable[object]]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def repo_over(collection: FakeCollection) -> TenantRepository:
    return make_store(FakeDatabase({collection.name: collection})).tenant(collection.name)


def scoped[T](org_id: str, op: Callable[[], Coroutine[Any, Any, T]]) -> T:
    async def main() -> T:
        with tenant_scope(org_id):
            return await op()

    return run(main())


# Every public data operation of the repository. A new operation must be added here, and
# test_every_public_operation_is_covered fails until it is.
OPERATIONS: dict[str, Op] = {
    "find": lambda r: r.find({"status": "active"}),
    "find_one": lambda r: r.find_one({"status": "active"}),
    "get": lambda r: r.get("h1"),
    "insert_one": lambda r: r.insert_one({"name": "x"}),
    "insert_many": lambda r: r.insert_many([{"name": "x"}]),
    "update_one": lambda r: r.update_one({"_id": "h1"}, {"$set": {"status": "done"}}),
    "update_many": lambda r: r.update_many({}, {"$set": {"status": "done"}}),
    "update_by_id": lambda r: r.update_by_id("h1", {"$set": {"status": "done"}}),
    "delete_one": lambda r: r.delete_one({"_id": "h1"}),
    "delete_by_id": lambda r: r.delete_by_id("h1"),
    "delete_many": lambda r: r.delete_many({"status": "expired"}),
    "aggregate": lambda r: r.aggregate([{"$sort": {"name": 1}}]),
    "count": lambda r: r.count({"status": "active"}),
}


def test_every_public_operation_is_covered() -> None:
    public = {
        name for name in dir(TenantRepository) if not name.startswith("_") and callable(getattr(TenantRepository, name))
    }
    assert public == set(OPERATIONS)


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_every_operation_raises_without_a_tenant_context(name: str) -> None:
    collection = FakeCollection(find_one_result={"_id": "h1"})
    repo = repo_over(collection)
    with pytest.raises(NoTenantContextError):
        run(_awaited(OPERATIONS[name](repo)))
    assert collection.calls == [], "the driver was called before the context check"


async def _awaited(value: Awaitable[object]) -> object:
    return await value


# --- filters -----------------------------------------------------------------------------


def test_find_adds_org_id_to_the_filter_and_leaves_the_caller_filter_alone() -> None:
    collection = FakeCollection(docs=[{"_id": "h1", "org_id": ORG}])
    repo = repo_over(collection)
    caller_filter = {"status": "active"}
    docs = scoped(ORG, lambda: repo.find(caller_filter, sort=[("name", 1)], skip=2, limit=5, projection={"name": 1}))
    call = collection.last("find")
    assert call.args[0] == {"status": "active", "org_id": ORG}
    assert call.kwargs == {"projection": {"name": 1}, "sort": [("name", 1)], "skip": 2, "limit": 5}
    assert caller_filter == {"status": "active"}
    assert docs == [{"_id": "h1", "org_id": ORG}]


def test_find_with_no_filter_is_scoped_to_the_org() -> None:
    collection = FakeCollection()
    scoped(ORG, lambda: repo_over(collection).find())
    assert collection.last("find").args[0] == {"org_id": ORG}


def test_find_one_and_count_are_scoped() -> None:
    collection = FakeCollection(find_one_result={"_id": "h1", "org_id": ORG}, count_result=3)
    repo = repo_over(collection)
    assert scoped(ORG, lambda: repo.find_one({"name": "x"})) == {"_id": "h1", "org_id": ORG}
    assert scoped(ORG, lambda: repo.count()) == 3
    assert collection.last("find_one").args[0] == {"name": "x", "org_id": ORG}
    assert collection.last("count_documents").args[0] == {"org_id": ORG}


def test_a_filter_naming_another_org_is_refused() -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError, match="org_id"):
        scoped(ORG, lambda: repo_over(collection).find({"org_id": OTHER}))
    assert collection.calls == []


def test_a_filter_naming_the_own_org_is_accepted() -> None:
    collection = FakeCollection()
    scoped(ORG, lambda: repo_over(collection).find({"org_id": ORG}))
    assert collection.last("find").args[0] == {"org_id": ORG}


def test_a_filter_with_an_operator_on_org_id_is_refused() -> None:
    with pytest.raises(TenantViolationError, match="org_id"):
        scoped(ORG, lambda: repo_over(FakeCollection()).find({"org_id": {"$in": [ORG, OTHER]}}))


def test_a_filter_must_be_a_mapping() -> None:
    with pytest.raises(TypeError, match="filter"):
        scoped(ORG, lambda: repo_over(FakeCollection()).find("not a filter"))  # type: ignore[arg-type]


# --- ids -----------------------------------------------------------------------------------


def test_get_looks_up_by_id_and_org() -> None:
    collection = FakeCollection(find_one_result={"_id": "h1", "org_id": ORG})
    doc = scoped(ORG, lambda: repo_over(collection).get("h1", projection={"name": 1}))
    assert doc == {"_id": "h1", "org_id": ORG}
    call = collection.last("find_one")
    assert call.args[0] == {"_id": "h1", "org_id": ORG}
    assert call.kwargs == {"projection": {"name": 1}}


def test_a_foreign_id_raises_not_found_not_a_permission_error() -> None:
    collection = FakeCollection(find_one_result=None)  # the {_id, org_id} lookup matches nothing
    with pytest.raises(NotFound) as err:
        scoped(ORG, lambda: repo_over(collection).get("belongs_to_org_b"))
    assert not isinstance(err.value, PermissionError)
    assert not issubclass(NotFound, PermissionError)
    assert NotFound is NotFoundError
    assert "org_b" not in str(err.value)


def test_update_by_id_and_delete_by_id_raise_not_found_for_a_foreign_id() -> None:
    collection = FakeCollection(matched_count=0, deleted_count=0)
    repo = repo_over(collection)
    with pytest.raises(NotFound):
        scoped(ORG, lambda: repo.update_by_id("h9", {"$set": {"status": "x"}}))
    with pytest.raises(NotFound):
        scoped(ORG, lambda: repo.delete_by_id("h9"))
    assert collection.last("update_one").args[0] == {"_id": "h9", "org_id": ORG}
    assert collection.last("delete_one").args[0] == {"_id": "h9", "org_id": ORG}


def test_update_by_id_and_delete_by_id_succeed_for_an_own_id() -> None:
    collection = FakeCollection()
    repo = repo_over(collection)
    outcome = scoped(ORG, lambda: repo.update_by_id("h1", {"$set": {"status": "x"}}))
    assert (outcome.matched, outcome.modified, outcome.upserted_id) == (1, 1, None)
    scoped(ORG, lambda: repo.delete_by_id("h1"))
    assert scoped(ORG, lambda: repo.delete_one({"name": "x"})) == 1
    assert collection.last("delete_one").args[0] == {"name": "x", "org_id": ORG}


def test_delete_many_deletes_only_the_current_orgs_matching_documents() -> None:
    # NOTIF-1: the sink sweeps its org's expired `pii_grant_jti` rows.
    collection = FakeCollection(deleted_count=3)
    assert scoped(ORG, lambda: repo_over(collection).delete_many({"expires_at": {"$lt": 5}})) == 3
    assert collection.last("delete_many").args[0] == {"expires_at": {"$lt": 5}, "org_id": ORG}


def test_delete_many_cannot_be_aimed_at_another_org() -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError):
        scoped(ORG, lambda: repo_over(collection).delete_many({"org_id": "org_b"}))
    assert collection.calls == []


# --- inserts -------------------------------------------------------------------------------


def test_insert_sets_org_id_without_mutating_the_caller_document() -> None:
    collection = FakeCollection()
    doc = {"_id": "h1", "name": "x"}
    inserted = scoped(ORG, lambda: repo_over(collection).insert_one(doc))
    assert inserted == "h1"
    assert collection.last("insert_one").args[0] == {"_id": "h1", "name": "x", "org_id": ORG}
    assert doc == {"_id": "h1", "name": "x"}


def test_insert_many_sets_org_id_on_every_document() -> None:
    collection = FakeCollection()
    ids = scoped(ORG, lambda: repo_over(collection).insert_many([{"_id": "a"}, {"_id": "b", "org_id": ORG}]))
    assert ids == ["a", "b"]
    assert collection.last("insert_many").args[0] == [{"_id": "a", "org_id": ORG}, {"_id": "b", "org_id": ORG}]


def test_inserting_a_document_of_another_org_is_refused() -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError, match="org_id"):
        scoped(ORG, lambda: repo_over(collection).insert_one({"name": "x", "org_id": OTHER}))
    with pytest.raises(TenantViolationError, match="org_id"):
        scoped(ORG, lambda: repo_over(collection).insert_many([{"name": "y"}, {"org_id": OTHER}]))
    assert collection.calls == []


def test_insert_needs_a_mapping_and_insert_many_a_non_empty_list() -> None:
    repo = repo_over(FakeCollection())
    with pytest.raises(TypeError, match="document"):
        scoped(ORG, lambda: repo.insert_one(["x"]))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        scoped(ORG, lambda: repo.insert_many([]))
    with pytest.raises(TypeError, match="list of documents"):
        scoped(ORG, lambda: repo.insert_many({"name": "x"}))  # type: ignore[arg-type]


# --- updates -------------------------------------------------------------------------------


def test_update_is_scoped_and_reports_its_outcome() -> None:
    collection = FakeCollection(matched_count=2)
    outcome = scoped(ORG, lambda: repo_over(collection).update_many({"s": 1}, {"$set": {"s": 2}}))
    assert (outcome.matched, outcome.modified) == (2, 2)
    call = collection.last("update_many")
    assert call.args == ({"s": 1, "org_id": ORG}, {"$set": {"s": 2}})


@pytest.mark.parametrize(
    "update",
    [
        {"$set": {"org_id": OTHER}},
        {"$set": {"org_id": ORG}},
        {"$set": {"org_id.x": 1}},
        {"$unset": {"org_id": ""}},
        {"$rename": {"org_id": "was_org"}},
        {"$rename": {"tenant": "org_id"}},
        {"$setOnInsert": {"org_id": OTHER}},
        {"$inc": {"n": 1}, "$set": {"org_id": OTHER}},
    ],
    ids=str,
)
def test_an_update_that_touches_org_id_is_refused(update: dict[str, Any]) -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError, match="org_id"):
        scoped(ORG, lambda: repo_over(collection).update_one({"_id": "h1"}, update))
    assert collection.calls == []


@pytest.mark.parametrize(
    "update",
    [{}, {"name": "replacement document"}, {"$set": "not a mapping"}, [{"$set": {"a": 1}}]],
    ids=str,
)
def test_only_operator_updates_are_accepted(update: Any) -> None:
    with pytest.raises((TypeError, ValueError), match="update"):
        scoped(ORG, lambda: repo_over(FakeCollection()).update_one({}, update))


def test_an_upsert_sets_org_id_on_insert() -> None:
    collection = FakeCollection(matched_count=0)
    update = {"$set": {"status": "new"}}
    outcome = scoped(ORG, lambda: repo_over(collection).update_one({"email_hash": "e1"}, update, upsert=True))
    call = collection.last("update_one")
    assert call.args == (
        {"email_hash": "e1", "org_id": ORG},
        {"$set": {"status": "new"}, "$setOnInsert": {"org_id": ORG}},
    )
    assert call.kwargs == {"upsert": True}
    assert outcome.upserted_id == "upserted-id"
    assert update == {"$set": {"status": "new"}}


def test_an_upsert_keeps_the_caller_set_on_insert_fields() -> None:
    collection = FakeCollection()
    update = {"$setOnInsert": {"created": 1}}
    scoped(ORG, lambda: repo_over(collection).update_one({}, update, upsert=True))
    assert collection.last("update_one").args[1] == {"$setOnInsert": {"created": 1, "org_id": ORG}}
    assert update == {"$setOnInsert": {"created": 1}}


def test_a_plain_update_does_not_upsert() -> None:
    collection = FakeCollection()
    scoped(ORG, lambda: repo_over(collection).update_one({}, {"$set": {"a": 1}}))
    call = collection.last("update_one")
    assert call.args[1] == {"$set": {"a": 1}}
    assert call.kwargs == {"upsert": False}


# --- pipelines -----------------------------------------------------------------------------


def test_aggregate_prepends_org_match() -> None:
    collection = FakeCollection(docs=[{"n": 1}])
    result = scoped(ORG, lambda: repo_over(collection).aggregate([{"$group": {"_id": None, "n": {"$sum": 1}}}]))
    assert result == [{"n": 1}]
    assert collection.last("aggregate").args[0] == [
        {"$match": {"org_id": ORG}},
        {"$group": {"_id": None, "n": {"$sum": 1}}},
    ]


def test_aggregate_prepends_org_match_before_a_leading_sort() -> None:
    """Trap from the card: `$match` must be first even when the caller passes `$sort` first."""
    collection = FakeCollection()
    pipeline: list[dict[str, Any]] = [{"$sort": {"name": 1}}, {"$match": {"status": "active"}}]
    scoped(ORG, lambda: repo_over(collection).aggregate(pipeline))
    sent = collection.last("aggregate").args[0]
    assert sent[0] == {"$match": {"org_id": ORG}}
    assert sent[1:] == [{"$sort": {"name": 1}}, {"$match": {"status": "active"}}]
    assert pipeline == [{"$sort": {"name": 1}}, {"$match": {"status": "active"}}]


def test_aggregate_of_an_empty_pipeline_is_still_scoped() -> None:
    collection = FakeCollection()
    scoped(ORG, lambda: repo_over(collection).aggregate([]))
    assert collection.last("aggregate").args[0] == [{"$match": {"org_id": ORG}}]


@pytest.mark.parametrize(
    "stage",
    [
        {"$lookup": {"from": "hires", "localField": "a", "foreignField": "b", "as": "c"}},
        {"$graphLookup": {"from": "hires"}},
        {"$unionWith": "hires"},
        {"$out": "elsewhere"},
        {"$merge": {"into": "elsewhere"}},
        {"$facet": {"x": [{"$unionWith": "hires"}]}},
        # Review finding: a tuple is encoded as a BSON array, so a tuple sub-pipeline must be walked too.
        {"$facet": {"x": ({"$unionWith": "hires"},)}},
        {"$facet": {"x": ({"$lookup": {"from": "hires", "pipeline": [], "as": "all"}},)}},
    ],
    ids=str,
)
def test_aggregate_refuses_stages_that_read_or_write_other_collections(stage: dict[str, Any]) -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError, match=r"\$"):
        scoped(ORG, lambda: repo_over(collection).aggregate([stage]))
    assert collection.calls == []


@pytest.mark.parametrize("pipeline", ["$match", [{"$match": {}, "$sort": {}}], ["not a stage"]], ids=str)
def test_aggregate_needs_a_list_of_single_stage_mappings(pipeline: Any) -> None:
    with pytest.raises(TypeError, match="pipeline"):
        scoped(ORG, lambda: repo_over(FakeCollection()).aggregate(pipeline))


def test_aggregate_sends_plain_lists_and_dicts_to_the_driver() -> None:
    collection = FakeCollection()
    pipeline = ({"$facet": {"x": ({"$match": {"a": 1}},)}},)
    scoped(ORG, lambda: repo_over(collection).aggregate(pipeline))
    assert collection.last("aggregate").args[0] == [
        {"$match": {"org_id": ORG}},
        {"$facet": {"x": [{"$match": {"a": 1}}]}},
    ]


@pytest.mark.parametrize(
    "stage",
    [{"$geoNear": {"near": [0, 0]}}, {"$search": {"text": {}}}, {"$documents": [{"a": 1}]}, {"$changeStream": {}}],
    ids=lambda s: next(iter(s)),
)
def test_aggregate_refuses_first_stage_only_operators_it_cannot_scope(stage: dict[str, Any]) -> None:
    collection = FakeCollection()
    with pytest.raises(TenantViolationError, match="first stage"):
        scoped(ORG, lambda: repo_over(collection).aggregate([stage]))
    assert collection.calls == []


class _SneakyKey(str):
    """A str subclass that hides from == and startswith but still encodes to BSON as "org_id"."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return hash("sneaky")

    def startswith(self, *args: Any, **kwargs: Any) -> bool:
        return False


SNEAKY = _SneakyKey("org_id")


@pytest.mark.parametrize(
    "op",
    [
        lambda r: r.find({SNEAKY: OTHER}),
        lambda r: r.insert_one({SNEAKY: OTHER}),
        lambda r: r.update_one({}, {"$set": {SNEAKY: OTHER}}),
        lambda r: r.aggregate([{"$match": {SNEAKY: OTHER}}]),
    ],
    ids=["filter", "document", "update", "pipeline"],
)
def test_keys_must_be_plain_strings(op: Op) -> None:
    collection = FakeCollection()
    with pytest.raises(TypeError, match="plain strings"):
        scoped(ORG, lambda: _awaited(op(repo_over(collection))))
    assert collection.calls == []


@pytest.mark.parametrize("bad_id", [{"$ne": None}, ["a", "b"], ("a",), {"a"}], ids=str)
@pytest.mark.parametrize("name", ["get", "update_by_id", "delete_by_id"])
def test_by_id_operations_refuse_an_operator_or_collection_as_the_id(name: str, bad_id: Any) -> None:
    collection = FakeCollection()
    repo = repo_over(collection)
    calls: dict[str, Callable[[], Coroutine[Any, Any, object]]] = {
        "get": lambda: repo.get(bad_id),
        "update_by_id": lambda: repo.update_by_id(bad_id, {"$set": {"a": 1}}),
        "delete_by_id": lambda: repo.delete_by_id(bad_id),
    }
    with pytest.raises(TypeError, match="single value"):
        scoped(ORG, calls[name])
    assert collection.calls == []


def test_the_repository_names_its_collection() -> None:
    assert repo_over(FakeCollection(name="run_tasks")).collection_name == "run_tasks"
