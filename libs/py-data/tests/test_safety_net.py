"""The runtime safety net: a command listener that flags unscoped commands (T-2; PRD §7.9 Safety net).

PyMongo swallows exceptions raised inside a listener, so the listener records each violation
(without any values from the command) and `check()` raises them; the integration fixture calls it
after every test.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from onb_data import UnscopedCommandError, safety_net
from onb_data.safety_net import (
    TenantCommandListener,
    Violation,
    find_index_violation,
    find_violation,
    install_safety_net,
)
from pymongo import monitoring
from pymongo.monitoring import CommandStartedEvent

DB = "app"
ORG = "org_alpha"


def started(command: dict[str, Any], database: str = DB) -> CommandStartedEvent:
    return CommandStartedEvent(command, database, 1, ("127.0.0.1", 27017), 1)


# --- find_violation: commands that carry an org-scoped filter pass -----------------------------

SCOPED_COMMANDS: dict[str, dict[str, Any]] = {
    "find": {"find": "hires", "filter": {"org_id": ORG, "name": "x"}},
    "find $eq": {"find": "hires", "filter": {"org_id": {"$eq": ORG}}},
    "find $and": {"find": "hires", "filter": {"$and": [{"name": "x"}, {"org_id": ORG}]}},
    "count": {"count": "hires", "query": {"org_id": ORG}},
    "distinct": {"distinct": "hires", "key": "name", "query": {"org_id": ORG}},
    "findAndModify": {"findAndModify": "hires", "query": {"org_id": ORG}, "update": {"$set": {"a": 1}}},
    "update": {"update": "hires", "updates": [{"q": {"org_id": ORG}, "u": {"$set": {"a": 1}}}]},
    "delete": {"delete": "hires", "deletes": [{"q": {"org_id": ORG, "_id": "a1"}, "limit": 1}]},
    "aggregate": {"aggregate": "hires", "pipeline": [{"$match": {"org_id": ORG}}, {"$sort": {"a": 1}}]},
    "insert": {"insert": "hires", "documents": [{"org_id": ORG, "name": "x"}, {"org_id": ORG}]},
    "createIndexes": {
        "createIndexes": "hires",
        "indexes": [{"key": {"org_id": 1, "email_hash": 1}, "name": "org_email", "unique": True}],
    },
}


@pytest.mark.parametrize("command", SCOPED_COMMANDS.values(), ids=SCOPED_COMMANDS.keys())
def test_a_scoped_command_on_a_tenant_collection_passes(command: dict[str, Any]) -> None:
    name = next(iter(command))
    assert find_violation(name, command, DB) is None


# --- find_violation: commands without an org scope are flagged ---------------------------------

UNSCOPED_COMMANDS: dict[str, dict[str, Any]] = {
    "find without filter": {"find": "hires"},
    "find empty filter": {"find": "hires", "filter": {}},
    "find other field": {"find": "hires", "filter": {"name": "x"}},
    "find org_id $ne": {"find": "hires", "filter": {"org_id": {"$ne": ORG}}},
    "find org_id $exists": {"find": "hires", "filter": {"org_id": {"$exists": True}}},
    "find org_id $in": {"find": "hires", "filter": {"org_id": {"$in": [ORG, "org_beta"]}}},
    "find org_id non-string": {"find": "hires", "filter": {"org_id": None}},
    "find $or with org": {"find": "hires", "filter": {"$or": [{"org_id": ORG}, {"name": "x"}]}},
    "find $and without org": {"find": "hires", "filter": {"$and": [{"name": "x"}]}},
    "count without query": {"count": "hires"},
    "distinct without query": {"distinct": "hires", "key": "name"},
    "findAndModify unscoped": {"findAndModify": "hires", "query": {"_id": "a1"}, "remove": True},
    "update one statement unscoped": {
        "update": "hires",
        "updates": [{"q": {"org_id": ORG}, "u": {}}, {"q": {}, "u": {"$set": {"a": 1}}}],
    },
    "delete unscoped": {"delete": "hires", "deletes": [{"q": {"_id": "b1"}, "limit": 1}]},
    "aggregate empty": {"aggregate": "hires", "pipeline": []},
    "aggregate match later": {"aggregate": "hires", "pipeline": [{"$sort": {"a": 1}}, {"$match": {"org_id": ORG}}]},
    "aggregate first match unscoped": {"aggregate": "hires", "pipeline": [{"$match": {"name": "x"}}]},
    "aggregate lookup to tenant": {
        "aggregate": "hires",
        "pipeline": [{"$match": {"org_id": ORG}}, {"$lookup": {"from": "tasks", "as": "t", "pipeline": []}}],
    },
    "aggregate nested unionWith": {
        "aggregate": "hires",
        "pipeline": [{"$match": {"org_id": ORG}}, {"$facet": {"x": [{"$unionWith": {"coll": "tasks"}}]}}],
    },
    "aggregate unionWith string": {
        "aggregate": "hires",
        "pipeline": [{"$match": {"org_id": ORG}}, {"$unionWith": "tasks"}],
    },
    "aggregate out": {"aggregate": "hires", "pipeline": [{"$match": {"org_id": ORG}}, {"$out": "copy"}]},
    "aggregate merge": {"aggregate": "hires", "pipeline": [{"$match": {"org_id": ORG}}, {"$merge": {"into": "x"}}]},
    "db aggregate unionWith tenant": {"aggregate": 1, "pipeline": [{"$documents": []}, {"$unionWith": "hires"}]},
    "insert without org": {"insert": "hires", "documents": [{"org_id": ORG}, {"name": "x"}]},
    "insert org non-string": {"insert": "hires", "documents": [{"org_id": {"$x": 1}}]},
    "createIndexes not org first": {
        "createIndexes": "hires",
        "indexes": [{"key": {"email_hash": 1, "org_id": 1}, "name": "email_org"}],
    },
    "mapReduce": {"mapReduce": "hires", "map": "f", "reduce": "g", "out": {"inline": 1}},
    "client bulkWrite on tenant ns": {"bulkWrite": 1, "ops": [], "nsInfo": [{"ns": "app.hires"}]},
}


@pytest.mark.parametrize("command", UNSCOPED_COMMANDS.values(), ids=UNSCOPED_COMMANDS.keys())
def test_an_unscoped_command_on_a_tenant_collection_is_flagged(command: dict[str, Any]) -> None:
    name = next(iter(command))
    reason = find_violation(name, command, DB)
    assert reason is not None
    assert ORG not in reason, "the reason never repeats values from the command"
    assert "org_beta" not in reason


OUT_OF_SCOPE: dict[str, tuple[dict[str, Any], str]] = {
    "global collection": ({"find": "orgs", "filter": {}}, DB),
    "agent registry": ({"update": "agent_registry", "updates": [{"q": {}, "u": {}}]}, DB),
    "system collection": ({"find": "system.views", "filter": {}}, DB),
    "admin database": ({"find": "hires", "filter": {}}, "admin"),
    "config database": ({"find": "chunks", "filter": {}}, "config"),
    "local database": ({"find": "oplog.rs", "filter": {}}, "local"),
    "ping": ({"ping": 1}, DB),
    "getMore": ({"getMore": 123, "collection": "hires"}, DB),
    "listIndexes": ({"listIndexes": "hires"}, DB),
    "drop": ({"drop": "hires"}, DB),
    "createIndexes on global": ({"createIndexes": "orgs", "indexes": [{"key": {"slug": 1}, "name": "s"}]}, DB),
    "bulkWrite on global ns": ({"bulkWrite": 1, "ops": [], "nsInfo": [{"ns": "app.orgs"}]}, "admin"),
}


@pytest.mark.parametrize(("command", "database"), OUT_OF_SCOPE.values(), ids=OUT_OF_SCOPE.keys())
def test_commands_outside_tenant_data_pass(command: dict[str, Any], database: str) -> None:
    assert find_violation(next(iter(command)), command, database) is None


def test_a_malformed_command_is_flagged_not_crashed() -> None:
    assert find_violation("update", {"update": "hires", "updates": "nope"}, DB) is not None
    assert find_violation("insert", {"insert": "hires", "documents": [1]}, DB) is not None
    assert find_violation("aggregate", {"aggregate": "hires", "pipeline": [{"$match": 1}]}, DB) is not None
    assert find_violation("createIndexes", {"createIndexes": "hires", "indexes": [{"key": {}}]}, DB) is not None
    assert find_violation("bulkWrite", {"bulkWrite": 1, "nsInfo": "x"}, "admin") is not None
    assert find_violation("bulkWrite", {"bulkWrite": 1, "nsInfo": [{"ns": "nodot"}]}, "admin") is not None
    assert find_violation("aggregate", {"aggregate": "hires"}, DB) is not None
    assert find_violation("insert", {"insert": "hires"}, DB) is not None
    assert find_violation("createIndexes", {"createIndexes": "hires"}, DB) is not None


def test_an_aggregate_on_a_global_collection_needs_no_org_match() -> None:
    command = {"aggregate": "orgs", "pipeline": [{"$sort": {"slug": 1}}]}
    assert find_violation("aggregate", command, DB) is None


def test_an_aggregate_may_look_up_a_global_collection() -> None:
    command = {
        "aggregate": "hires",
        "pipeline": [{"$match": {"org_id": ORG}}, {"$lookup": {"from": "orgs", "as": "o", "pipeline": []}}],
    }
    assert find_violation("aggregate", command, DB) is None


def test_the_id_index_is_the_only_index_not_led_by_org_id() -> None:
    assert find_index_violation("hires", {"_id": 1}) is None
    assert find_index_violation("hires", {"org_id": 1, "email_hash": 1}) is None
    assert find_index_violation("hires", {"email_hash": 1}) is not None
    assert find_index_violation("hires", {"_id": 1, "name": 1}) is not None


# --- the listener -------------------------------------------------------------------------------


def test_the_listener_records_an_unscoped_command_and_check_raises_it() -> None:
    listener = TenantCommandListener()
    listener.started(started({"find": "hires", "filter": {}}))
    recorded = listener.violations
    assert len(recorded) == 1
    assert recorded[0] == Violation(database=DB, collection="hires", command="find", reason=recorded[0].reason)
    with pytest.raises(UnscopedCommandError, match=r"find on app\.hires"):
        listener.check()
    assert not listener.violations, "check() drains what it reported"
    listener.check()


def test_the_listener_ignores_scoped_commands() -> None:
    listener = TenantCommandListener()
    for command in SCOPED_COMMANDS.values():
        listener.started(started(command))
    assert listener.violations == ()
    listener.check()


def test_the_listener_logs_violations_without_values(caplog: pytest.LogCaptureFixture) -> None:
    listener = TenantCommandListener()
    with caplog.at_level(logging.ERROR, logger="onb_data.safety_net"):
        listener.started(started({"find": "hires", "filter": {"email": "secret@example.test"}}))
    assert "unscoped find on app.hires" in caplog.text
    assert "secret@example.test" not in caplog.text


def test_drain_returns_and_clears() -> None:
    listener = TenantCommandListener()
    listener.started(started({"delete": "hires", "deletes": [{"q": {}, "limit": 0}]}))
    drained = listener.drain()
    assert [v.command for v in drained] == ["delete"]
    assert listener.drain() == ()


def test_allowing_unscoped_is_explicit_and_scoped_to_the_block() -> None:
    listener = TenantCommandListener()
    with listener.allowing_unscoped("root verification read"):
        listener.started(started({"find": "hires", "filter": {}}))
    assert listener.violations == ()
    listener.started(started({"find": "hires", "filter": {}}))
    assert len(listener.violations) == 1


def test_allowing_unscoped_needs_a_reason() -> None:
    listener = TenantCommandListener()
    with pytest.raises(ValueError, match="reason"), listener.allowing_unscoped(""):
        pass


def test_allowing_unscoped_does_not_leak_to_other_threads() -> None:
    listener = TenantCommandListener()

    def other_thread() -> None:
        listener.started(started({"find": "hires", "filter": {}}))

    with listener.allowing_unscoped("this thread only"):
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
    assert len(listener.violations) == 1


def test_success_and_failure_events_are_ignored() -> None:
    listener = TenantCommandListener()
    listener.succeeded(object())  # type: ignore[arg-type]  # the listener must not touch these events
    listener.failed(object())  # type: ignore[arg-type]  # the listener must not touch these events
    assert listener.violations == ()


def test_install_registers_one_global_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety_net, "_installed", None)
    before = list(monitoring._LISTENERS.command_listeners)
    try:
        listener = install_safety_net()
        assert isinstance(listener, TenantCommandListener)
        assert monitoring._LISTENERS.command_listeners[-1] is listener
        assert install_safety_net() is listener, "installing twice returns the same listener"
        assert monitoring._LISTENERS.command_listeners.count(listener) == 1
    finally:
        monitoring._LISTENERS.command_listeners[:] = before
