"""The least-privilege users against a real MongoDB (PRD §7.5 defence in depth).

Each test bypasses the policy layer on purpose: it talks to MongoDB as an MCP user through the
same `TenantRepository` the tools use, or runs a tool handler with the policy checks replaced by
no-ops. MongoDB itself must refuse (error code 13, Unauthorized).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Coroutine, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pytest
from onb_data import APPEND_ONLY_COLLECTIONS, DataStore, connect, tenant_scope
from onb_data.safety_net import install_safety_net
from onb_mcp_mongo import policy, tools
from onb_mcp_mongo.call_context import CallContext
from onb_mcp_mongo.db_users import (
    DENIED_COLLECTIONS,
    READ_ONLY,
    READ_WRITE,
    AccessSpec,
    PrivilegeError,
    assert_least_privilege,
    provision,
)
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from .conftest import DATABASE

UNAUTHORIZED = 13
ORG = "org_a"
RootClient = MongoClient[dict[str, Any]]
# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a deliberate unscoped command.
Unscoped = Callable[[str], AbstractContextManager[None]]


@dataclass(frozen=True)
class Users:
    read_uri: str
    write_uri: str

    def uri(self, spec: AccessSpec) -> str:
        return self.read_uri if spec is READ_ONLY else self.write_uri


def _user_uri(root_uri: str, user: str, password: str) -> str:
    netloc = urlsplit(root_uri).netloc.rsplit("@", 1)[-1]
    return f"mongodb://{user}:{password}@{netloc}/?authSource=admin"


@pytest.fixture(scope="session")
def users(mongo_uri: str) -> Iterator[Users]:
    read_password, write_password = secrets.token_hex(16), secrets.token_hex(16)
    root: RootClient = MongoClient(mongo_uri)
    try:
        provision(root["admin"], DATABASE, read_password=read_password, write_password=write_password)
        provision(root["admin"], DATABASE, read_password=read_password, write_password=write_password)  # again
        db = root[DATABASE]
        for name in DENIED_COLLECTIONS:
            db[name].insert_one({"org_id": ORG, "marker": f"secret-{name}"})
        db["run_tasks"].insert_one({"org_id": ORG, "task": "seed", "state": "pending"})
        db["new_hires"].insert_one({"org_id": ORG, "name_token": "ptk_seed"})
        yield Users(
            _user_uri(mongo_uri, READ_ONLY.user, read_password),
            _user_uri(mongo_uri, READ_WRITE.user, write_password),
        )
    finally:
        root.close()


def run[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(make())


async def _as_user(uri: str, work: Callable[[DataStore], Coroutine[Any, Any, Any]]) -> Any:
    store = connect(uri, DATABASE, app_name="mcp-mongo-int")
    try:
        with tenant_scope(ORG):
            return await work(store)
    finally:
        await store.close()


def _refused(uri: str, work: Callable[[DataStore], Coroutine[Any, Any, Any]]) -> None:
    with pytest.raises(OperationFailure) as caught:
        run(lambda: _as_user(uri, work))
    assert caught.value.code == UNAUTHORIZED


def _writable(store: DataStore, collection: str) -> Any:
    """The repository the app would write `collection` through: `audit_log` has only `append_only()` (AUD-1)."""
    return store.append_only(collection) if collection in APPEND_ONLY_COLLECTIONS else store.tenant(collection)


def _count(root_db: Any, name: str, query: dict[str, Any]) -> int:
    with install_safety_net().allowing_unscoped("root count across orgs proves the refused write left nothing"):
        return int(root_db[name].count_documents(query))


# --- Box 1: with the policy layer bypassed, a write using the read-only user is refused ---------


@pytest.mark.parametrize("collection", ["run_tasks", "new_hires", "audit_log"])
def test_the_read_only_user_cannot_insert_with_the_policy_bypassed(users: Users, root_db: Any, collection: str) -> None:
    _refused(users.read_uri, lambda s: _writable(s, collection).insert_one({"task": "evil-insert"}))
    assert _count(root_db, collection, {"task": "evil-insert"}) == 0


def test_the_read_only_user_cannot_update_with_the_policy_bypassed(users: Users, root_db: Any) -> None:
    _refused(
        users.read_uri,
        lambda s: s.tenant("run_tasks").update_one({"task": "seed"}, {"$set": {"state": "evil"}}),
    )
    assert _count(root_db, "run_tasks", {"state": "evil"}) == 0


def _bypass_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    for check in ("check_readable", "check_writable", "reject_org_id", "reject_id", "check_update_operators"):
        monkeypatch.setattr(policy, check, lambda *a, **k: None)


def _ctx(tool: str, *, read: set[str] | None = None, write: set[str] | None = None) -> CallContext:
    return CallContext(
        org_id=ORG,
        agent="db_agent",
        run_id="run_1",
        tool=tool,
        read=frozenset(read or set()),
        write=frozenset(write or set()),
    )


def test_an_insert_tool_on_the_read_only_store_is_refused_by_mongodb(
    users: Users, root_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wiring or policy bug that sends a write through the read-only connection still fails."""
    _bypass_policy(monkeypatch)
    _refused(
        users.read_uri,
        lambda s: tools.insert_one(s, _ctx("insert_one"), collection="run_tasks", document={"task": "evil-tool"}),
    )
    assert _count(root_db, "run_tasks", {"task": "evil-tool"}) == 0


# --- Box 2: no MCP user can read the vault, the keys, the PII access log or users ---------------


@pytest.mark.parametrize("collection", sorted(DENIED_COLLECTIONS))
@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_no_mcp_user_can_find_in_a_denied_collection(users: Users, spec: AccessSpec, collection: str) -> None:
    _refused(users.uri(spec), lambda s: s.tenant(collection).find({}, limit=1))


@pytest.mark.parametrize("collection", sorted(DENIED_COLLECTIONS))
@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_no_mcp_user_can_count_or_aggregate_a_denied_collection(
    users: Users, spec: AccessSpec, collection: str
) -> None:
    _refused(users.uri(spec), lambda s: s.tenant(collection).count({}))
    _refused(users.uri(spec), lambda s: s.tenant(collection).aggregate([{"$limit": 1}]))


@pytest.mark.parametrize("collection", sorted(DENIED_COLLECTIONS))
@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_a_find_tool_on_a_denied_collection_with_the_policy_bypassed_is_refused(
    users: Users, spec: AccessSpec, collection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a forged context naming the vault, with every policy check off, reads nothing."""
    _bypass_policy(monkeypatch)
    ctx = _ctx("find", read={collection})
    _refused(
        users.uri(spec),
        lambda s: tools.find(s, ctx, collection=collection, filter={}, projection=None, sort=None, skip=0, limit=1),
    )


CROSS_COLLECTION_STAGES: dict[str, list[dict[str, Any]]] = {
    "lookup": [{"$lookup": {"from": "pii_vault", "localField": "org_id", "foreignField": "org_id", "as": "v"}}],
    "graphLookup": [
        {
            "$graphLookup": {
                "from": "org_keys",
                "startWith": "$org_id",
                "connectFromField": "org_id",
                "connectToField": "org_id",
                "as": "k",
            }
        }
    ],
    "unionWith": [{"$unionWith": {"coll": "pii_vault_aadhaar"}}],
    "out": [{"$out": "users"}],
    "merge": [{"$merge": {"into": "pii_access_log"}}],
}


@pytest.mark.parametrize("stage", sorted(CROSS_COLLECTION_STAGES))
@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_no_pipeline_stage_reaches_a_denied_collection_with_a_raw_driver(
    users: Users, root_db: Any, spec: AccessSpec, stage: str, unscoped: Unscoped
) -> None:
    """No policy and no repository: the MCP user runs the stage straight from a readable collection."""
    client: RootClient = MongoClient(users.uri(spec))
    try:
        with (
            unscoped("deliberate raw-driver cross-collection stage: MongoDB itself must refuse it"),
            pytest.raises(OperationFailure) as caught,
        ):
            list(client[DATABASE]["run_tasks"].aggregate(CROSS_COLLECTION_STAGES[stage]))
    finally:
        client.close()
    assert caught.value.code == UNAUTHORIZED
    assert _count(root_db, "users", {"task": "seed"}) == 0
    assert _count(root_db, "pii_access_log", {"task": "seed"}) == 0


def test_the_read_write_user_cannot_decide_an_approval(users: Users, root_db: Any) -> None:
    root_db["approvals"].insert_one({"org_id": ORG, "approval": "seed", "status": "pending"})
    _refused(
        users.write_uri,
        lambda s: s.tenant("approvals").update_one({"approval": "seed"}, {"$set": {"status": "approved"}}),
    )
    assert _count(root_db, "approvals", {"approval": "seed", "status": "pending"}) == 1


def test_the_read_write_user_cannot_write_into_a_denied_collection(users: Users, root_db: Any) -> None:
    _refused(users.write_uri, lambda s: s.tenant("pii_vault").insert_one({"marker": "evil-vault"}))
    assert _count(root_db, "pii_vault", {"marker": "evil-vault"}) == 0


# --- Controls: the users still do their job, and nothing beyond it ------------------------------


def test_the_read_only_user_reads_its_collections(users: Users) -> None:
    docs = run(lambda: _as_user(users.read_uri, lambda s: s.tenant("run_tasks").find({"task": "seed"}, limit=5)))
    assert [d["state"] for d in docs] == ["pending"]


def test_the_read_only_user_counts_aggregates_and_sorts_its_collections(users: Users) -> None:
    async def work(store: DataStore) -> tuple[int, list[Any], list[Any]]:
        repo = store.tenant("run_tasks")
        total = await repo.count({})
        groups = await repo.aggregate([{"$group": {"_id": "$state", "n": {"$sum": 1}}}])
        page = await repo.find({}, projection={"task": 1}, sort=[("task", 1)], skip=0, limit=5)
        return total, groups, page

    total, groups, page = run(lambda: _as_user(users.read_uri, work))
    assert total >= 1
    assert groups
    assert page


def test_the_write_tools_work_on_the_read_write_store(users: Users, root_db: Any) -> None:
    ctx_insert = _ctx("insert_one", write={"run_tasks"})
    ctx_update = _ctx("update_one", write={"run_tasks"})

    async def work(store: DataStore) -> None:
        await tools.insert_one(store, ctx_insert, collection="run_tasks", document={"task": "tool-ok", "state": "new"})
        await tools.update_one(
            store, ctx_update, collection="run_tasks", filter={"task": "tool-ok"}, update={"$set": {"state": "done"}}
        )

    run(lambda: _as_user(users.write_uri, work))
    assert _count(root_db, "run_tasks", {"task": "tool-ok", "state": "done", "org_id": ORG}) == 1


def test_the_read_write_user_inserts_and_updates_its_collections(users: Users, root_db: Any) -> None:
    async def work(store: DataStore) -> None:
        await store.tenant("run_tasks").insert_one({"task": "rw-ok", "state": "new"})
        await store.tenant("run_tasks").update_one({"task": "rw-ok"}, {"$set": {"state": "done"}})

    run(lambda: _as_user(users.write_uri, work))
    assert _count(root_db, "run_tasks", {"task": "rw-ok", "state": "done", "org_id": ORG}) == 1


def test_the_read_write_user_cannot_write_outside_its_collections(users: Users, root_db: Any) -> None:
    _refused(users.write_uri, lambda s: s.append_only("audit_log").insert_one({"event": "evil-audit"}))
    _refused(users.write_uri, lambda s: s.tenant("templates").insert_one({"event": "evil-template"}))
    assert _count(root_db, "audit_log", {"event": "evil-audit"}) == 0


@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_no_mcp_user_can_delete(users: Users, root_db: Any, spec: AccessSpec) -> None:
    _refused(users.uri(spec), lambda s: s.tenant("run_tasks").delete_one({"task": "seed"}))
    assert _count(root_db, "run_tasks", {"task": "seed"}) == 1


def test_the_read_only_user_cannot_read_outside_its_collections(users: Users) -> None:
    _refused(users.read_uri, lambda s: s.append_only("audit_log").find({}, limit=1))


@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_each_user_passes_the_start_up_privilege_check(users: Users, spec: AccessSpec) -> None:
    assert_least_privilege(users.uri(spec), spec, DATABASE)


def test_the_start_up_check_refuses_the_read_only_user_granted_an_extra_role(users: Users, mongo_uri: str) -> None:
    root: RootClient = MongoClient(mongo_uri)
    extra = [{"role": "readWrite", "db": DATABASE}]
    try:
        root["admin"].command("grantRolesToUser", READ_ONLY.user, roles=extra)
        with pytest.raises(PrivilegeError, match="outside its role"):
            assert_least_privilege(users.read_uri, READ_ONLY, DATABASE)
    finally:
        root["admin"].command("revokeRolesFromUser", READ_ONLY.user, roles=extra)
        root.close()
    assert_least_privilege(users.read_uri, READ_ONLY, DATABASE)


def test_the_start_up_check_refuses_root_and_the_other_user(users: Users, mongo_uri: str) -> None:
    with pytest.raises(PrivilegeError):
        assert_least_privilege(mongo_uri, READ_ONLY, DATABASE)
    with pytest.raises(PrivilegeError):
        assert_least_privilege(users.write_uri, READ_ONLY, DATABASE)
