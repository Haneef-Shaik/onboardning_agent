"""The audit log: one `audit(...)` call writes an append-only, org-scoped record (AUD-1; PRD §7.6, §7.9).

Box 1: the audit repository has no update or delete path. `store.tenant("audit_log")` is refused, so
the only handle on `audit_log` is the append-only repository, whose surface is insert, find and count.
Box 3: a record carries the current span's trace id.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from onb_data import (
    APPEND_ONLY_COLLECTIONS,
    GLOBAL_COLLECTIONS,
    QUEUE_COLLECTIONS,
    Actor,
    ActorType,
    AppendOnlyRepository,
    AuditLog,
    AuditOutcome,
    AuditQuery,
    NoTenantContextError,
    TenantViolationError,
    UnknownCollectionError,
    tenant_scope,
)
from onb_data import audit as audit_module
from onb_data.repository import _Repository
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from .fakes import FakeCollection, FakeDatabase, make_store

ORG = "org_acme"
NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
WRITE_WORDS = ("update", "delete", "replace", "remove", "drop", "upsert", "modify", "rename", "aggregate")


def _fixed_clock() -> datetime:
    return NOW


def _log(database: FakeDatabase | None = None, **kwargs: Any) -> tuple[AuditLog, FakeCollection]:
    db = database if database is not None else FakeDatabase()
    kwargs.setdefault("clock", _fixed_clock)
    return AuditLog(make_store(db), **kwargs), db["audit_log"]


def _record(log: AuditLog, **overrides: Any) -> str:
    fields: dict[str, Any] = {
        "actor": Actor.user("u_acme_hr1"),
        "action": "pii.reveal",
        "target": "hire:h_123/pan",
        "outcome": AuditOutcome.SUCCESS,
        "reason": "verifying documents",
    }
    fields.update(overrides)

    async def run() -> str:
        with tenant_scope(ORG):
            return await log.audit(**fields)

    return asyncio.run(run())


def _public_names(obj: object) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


# --- Box 1: no update or delete path ---------------------------------------------------------------


def test_audit_log_is_the_only_append_only_collection() -> None:
    assert frozenset({"audit_log"}) == APPEND_ONLY_COLLECTIONS
    assert not APPEND_ONLY_COLLECTIONS & GLOBAL_COLLECTIONS
    assert not APPEND_ONLY_COLLECTIONS & QUEUE_COLLECTIONS


@pytest.mark.parametrize("handout", ["tenant", "shared", "queue"])
def test_the_audit_collection_is_never_handed_out_with_a_write_path(handout: str) -> None:
    with pytest.raises(UnknownCollectionError):
        getattr(make_store(), handout)("audit_log")


def test_append_only_is_refused_for_other_collections() -> None:
    with pytest.raises(UnknownCollectionError):
        make_store().append_only("hires")


def test_the_append_only_repository_has_no_update_or_delete_path() -> None:
    repo = make_store().append_only("audit_log")
    assert isinstance(repo, AppendOnlyRepository)
    assert _Repository not in type(repo).__mro__
    assert repo.collection_name == "audit_log"
    assert _public_names(repo) == {"collection_name", "count", "find", "insert_one"}
    assert _public_names(AppendOnlyRepository) == {"collection_name", "count", "find", "insert_one"}


def test_the_audit_log_has_no_update_or_delete_path() -> None:
    log, _ = _log()
    assert _public_names(AuditLog) == {"audit", "ensure_indexes", "list"}
    for name in _public_names(log):
        assert not any(word in name.lower() for word in WRITE_WORDS), name
        value = getattr(log, name)
        assert not isinstance(value, _Repository | AppendOnlyRepository), name


def test_no_attribute_reachable_from_the_audit_log_is_a_full_repository() -> None:
    """Walk every attribute (slots included, private too) one level down: no update/delete-capable repo."""
    log, _ = _log()
    seen: list[object] = []
    pending: list[object] = [log]
    while pending:
        obj = pending.pop()
        if any(obj is s for s in seen):
            continue
        seen.append(obj)
        assert not isinstance(obj, _Repository), type(obj).__name__
        if isinstance(obj, AuditLog | AppendOnlyRepository):
            pending.extend(getattr(obj, slot) for slot in type(obj).__slots__)


def test_the_audit_log_never_calls_a_driver_write_other_than_insert() -> None:
    db = FakeDatabase()
    log, coll = _log(db)
    _record(log)

    async def read() -> None:
        with tenant_scope(ORG):
            await log.list(AuditQuery(), page=1, limit=10)
            await log.ensure_indexes()

    asyncio.run(read())
    methods = {call.method for call in coll.calls}
    assert methods <= {"insert_one", "find", "count_documents", "create_index"}
    assert "insert_one" in methods


# --- One audit(...) call: the record -------------------------------------------------------------


def test_one_audit_call_writes_one_org_scoped_record() -> None:
    log, coll = _log()
    record_id = _record(log)
    document = coll.last("insert_one").args[0]
    assert document == {
        "_id": record_id,
        "org_id": ORG,
        "actor": {"type": "user", "id": "u_acme_hr1"},
        "action": "pii.reveal",
        "target": "hire:h_123/pan",
        "outcome": "success",
        "reason": "verifying documents",
        "trace_id": None,
        "at": NOW,
    }
    assert len([c for c in coll.calls if c.method == "insert_one"]) == 1


def test_record_ids_are_unguessable_and_distinct() -> None:
    log, _ = _log()
    first, second = _record(log), _record(log)
    assert first != second
    assert len(first) == 32
    int(first, 16)


def test_an_agent_can_be_the_actor_and_reason_is_optional() -> None:
    log, coll = _log()
    _record(log, actor=Actor.agent("db_agent"), outcome=AuditOutcome.DENIED, reason=None, action="mcp.tool_call")
    document = coll.last("insert_one").args[0]
    assert document["actor"] == {"type": "agent", "id": "db_agent"}
    assert document["outcome"] == "denied"
    assert document["reason"] is None


def test_the_default_clock_is_utc_now() -> None:
    db = FakeDatabase()
    _record(AuditLog(make_store(db)))
    at = db["audit_log"].last("insert_one").args[0]["at"]
    assert at.tzinfo is UTC
    assert abs(datetime.now(UTC) - at) < timedelta(seconds=5)


def test_audit_without_a_tenant_context_raises_and_writes_nothing() -> None:
    log, coll = _log()

    async def run() -> None:
        await log.audit(actor=Actor.user("u1"), action="pii.reveal", target="t", outcome=AuditOutcome.SUCCESS)

    with pytest.raises(NoTenantContextError):
        asyncio.run(run())
    assert coll.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": ""},
        {"action": "Not Dotted"},
        {"action": "pii..reveal"},
        {"action": "a" * 65},
        {"target": ""},
        {"target": "x" * 257},
        {"reason": "x" * 1001},
        {"reason": ""},
        {"outcome": "success"},
        {"actor": ("user", "u1")},
    ],
    ids=lambda o: next(iter(o)),
)
def test_bad_fields_are_refused_before_the_driver(overrides: dict[str, Any]) -> None:
    log, coll = _log()
    with pytest.raises((ValueError, TypeError)):
        _record(log, **overrides)
    assert coll.calls == []


@pytest.mark.parametrize("actor_id", ["", "has space", "x" * 129, "a$b"])
def test_bad_actor_ids_are_refused(actor_id: str) -> None:
    with pytest.raises(ValueError, match="actor id"):
        Actor.user(actor_id)


def test_an_actor_type_must_be_an_actor_type() -> None:
    with pytest.raises(TypeError):
        Actor(type=cast(Any, "user"), id="u1")


def test_a_record_cannot_name_another_org() -> None:
    log, coll = _log()

    async def run() -> None:
        with tenant_scope(ORG):
            await log._repo.insert_one({"org_id": "org_globex", "action": "x"})

    with pytest.raises(TenantViolationError):
        asyncio.run(run())
    assert coll.calls == []


# --- Box 3: the current span's trace id ----------------------------------------------------------


@pytest.fixture
def tracer() -> trace.Tracer:
    return TracerProvider().get_tracer("aud-1-test")


def test_a_record_carries_the_current_spans_trace_id(tracer: trace.Tracer) -> None:
    log, coll = _log()

    async def run() -> int:
        with tracer.start_as_current_span("pii.reveal") as span, tenant_scope(ORG):
            await log.audit(
                actor=Actor.user("u_acme_hr1"), action="pii.reveal", target="hire:h_1", outcome=AuditOutcome.SUCCESS
            )
            return span.get_span_context().trace_id

    trace_id = asyncio.run(run())
    written = coll.last("insert_one").args[0]["trace_id"]
    assert written == f"{trace_id:032x}"
    assert len(written) == 32
    assert written != "0" * 32


def test_two_spans_give_two_trace_ids(tracer: trace.Tracer) -> None:
    log, coll = _log()
    ids = []
    for _ in range(2):
        with tracer.start_as_current_span("op") as span:
            _record(log)
            ids.append(f"{span.get_span_context().trace_id:032x}")
    written = [c.args[0]["trace_id"] for c in coll.calls if c.method == "insert_one"]
    assert written == ids
    assert ids[0] != ids[1]


def test_a_record_outside_any_span_has_no_trace_id() -> None:
    log, coll = _log()
    _record(log)
    assert coll.last("insert_one").args[0]["trace_id"] is None


def test_current_trace_id_reads_the_active_span(tracer: trace.Tracer) -> None:
    assert audit_module.current_trace_id() is None
    with tracer.start_as_current_span("op") as span:
        assert audit_module.current_trace_id() == f"{span.get_span_context().trace_id:032x}"


# --- Reading: filters and pagination ------------------------------------------------------------


def _list(log: AuditLog, query: AuditQuery, *, page: int = 1, limit: int = 20) -> Any:
    async def run() -> Any:
        with tenant_scope(ORG):
            return await log.list(query, page=page, limit=limit)

    return asyncio.run(run())


def test_list_sends_a_scoped_filter_newest_first_with_skip_and_limit() -> None:
    db = FakeDatabase()
    coll = db["audit_log"]
    coll.count_result = 42
    coll.docs = [
        {
            "_id": "r1",
            "org_id": ORG,
            "actor": {"type": "agent", "id": "db_agent"},
            "action": "mcp.tool_call",
            "target": "find:hires",
            "outcome": "denied",
            "reason": "collection outside manifest",
            "trace_id": "ab" * 16,
            "at": NOW,
        }
    ]
    log, _ = _log(db)
    query = AuditQuery(
        actor_type=ActorType.AGENT,
        actor_id="db_agent",
        action="mcp.tool_call",
        outcome=AuditOutcome.DENIED,
        target="find:hires",
        since=NOW - timedelta(days=1),
        until=NOW,
    )
    page = _list(log, query, page=3, limit=10)
    find = coll.last("find")
    assert find.args[0] == {
        "actor.type": "agent",
        "actor.id": "db_agent",
        "action": "mcp.tool_call",
        "outcome": "denied",
        "target": "find:hires",
        "at": {"$gte": NOW - timedelta(days=1), "$lte": NOW},
        "org_id": ORG,
    }
    assert find.kwargs["sort"] == [("at", -1), ("_id", -1)]
    assert find.kwargs["skip"] == 20
    assert find.kwargs["limit"] == 10
    assert coll.last("count_documents").args[0]["org_id"] == ORG
    assert page.total == 42
    (entry,) = page.entries
    assert entry.id == "r1"
    assert entry.org_id == ORG
    assert entry.actor == Actor.agent("db_agent")
    assert entry.outcome is AuditOutcome.DENIED
    assert entry.trace_id == "ab" * 16
    assert entry.at == NOW


def test_an_empty_query_filters_on_the_org_only() -> None:
    log, coll = _log()
    _list(log, AuditQuery())
    assert coll.last("find").args[0] == {"org_id": ORG}


def test_only_since_or_only_until() -> None:
    log, coll = _log()
    _list(log, AuditQuery(since=NOW))
    assert coll.last("find").args[0]["at"] == {"$gte": NOW}
    _list(log, AuditQuery(until=NOW))
    assert coll.last("find").args[0]["at"] == {"$lte": NOW}


@pytest.mark.parametrize(("page", "limit"), [(0, 10), (1, 0), (1, 201), (-1, 10)])
def test_bad_pages_are_refused(page: int, limit: int) -> None:
    log, coll = _log()
    with pytest.raises(ValueError, match=r"page|limit"):
        _list(log, AuditQuery(), page=page, limit=limit)
    assert coll.calls == []


def test_a_query_refuses_operator_values() -> None:
    with pytest.raises(TypeError):
        AuditQuery(action=cast(Any, {"$ne": "x"}))
    with pytest.raises(ValueError, match="since must not be after until"):
        AuditQuery(since=NOW, until=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="time zone"):
        AuditQuery(since=datetime(2026, 1, 1))  # naive


def test_list_without_a_tenant_context_raises() -> None:
    log, coll = _log()

    async def run() -> None:
        await log.list(AuditQuery(), page=1, limit=10)

    with pytest.raises(NoTenantContextError):
        asyncio.run(run())
    assert coll.calls == []


def test_ensure_indexes_makes_an_org_prefixed_index() -> None:
    log, coll = _log()
    asyncio.run(log.ensure_indexes())
    keys = coll.last("create_index").args[0]
    assert keys[0] == ("org_id", 1)
    assert ("at", -1) in keys


@pytest.mark.parametrize(
    "build",
    [
        lambda: Actor.user(cast(Any, 7)),
        lambda: AuditQuery(target=cast(Any, 7)),
        lambda: AuditQuery(until=cast(Any, "2026-11-10")),
    ],
    ids=["actor-id", "target", "until"],
)
def test_non_string_and_non_datetime_values_are_type_errors(build: Any) -> None:
    with pytest.raises(TypeError):
        build()


def test_list_needs_an_audit_query() -> None:
    log, coll = _log()
    with pytest.raises(TypeError, match="AuditQuery"):
        _list(log, cast(Any, {"action": "x"}))
    assert coll.calls == []
