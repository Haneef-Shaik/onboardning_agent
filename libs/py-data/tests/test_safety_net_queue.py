"""The safety net and the worker queue agree (T-2 with EVT-1; ADR 0002).

Workers reach `jobs` and `dead_letters` across orgs through `QueueRepository`. The net allows exactly
the commands that repository sends, matched by collection and shape, and still flags every other
unscoped command on those collections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from onb_data.safety_net import find_index_violation, find_violation

DB = "app"
NOW = datetime(2026, 11, 10, tzinfo=UTC)
LEASED = {"_id": "job-1", "lease_token": "tok-1"}
CLAIM_QUERY = {
    "consumer": {"$in": ["orchestrator.start_run"]},
    "attempts": {"$type": "number"},
    "max_attempts": {"$type": "number"},
    "$or": [
        {"status": "pending", "available_at": {"$lte": NOW}},
        {"status": "running", "locked_until": {"$lte": NOW}},
    ],
}
CLAIM_UPDATE = {
    "$set": {
        "status": "running",
        "lease_token": "tok-1",
        "lease_owner": "w1",
        "locked_until": NOW,
        "claimed_at": NOW,
        "updated_at": NOW,
    },
    "$inc": {"attempts": 1},
}
RELEASE = {"lease_token": "", "lease_owner": "", "locked_until": ""}


def claim(**overrides: Any) -> dict[str, Any]:
    return {"findAndModify": "jobs", "query": CLAIM_QUERY, "update": CLAIM_UPDATE, "new": True, **overrides}


def leased_update(u: Any, collection: str = "jobs", **statement: Any) -> dict[str, Any]:
    return {"update": collection, "ordered": True, "updates": [{"q": LEASED, "u": u, **statement}]}


def leased_delete(collection: str = "jobs", **statement: Any) -> dict[str, Any]:
    return {"delete": collection, "ordered": True, "deletes": [{"q": LEASED, "limit": 1, **statement}]}


def orphan_letter(**overrides: Any) -> dict[str, Any]:
    letter = {"_id": "job-1", "consumer": "c.x", "status": "dead", "reason": "missing_org_id", "attempts": 1}
    return {"insert": "dead_letters", "ordered": True, "documents": [{**letter, **overrides}]}


# --- exactly what QueueRepository sends passes ---------------------------------------------------

ALLOWED: dict[str, dict[str, Any]] = {
    "claim": claim(),
    "claim with sort": claim(sort={"available_at": 1}),
    "heartbeat": leased_update({"$set": {"locked_until": NOW, "updated_at": NOW}}),
    "complete": leased_update({"$set": {"status": "done", "completed_at": NOW, "updated_at": NOW}, "$unset": RELEASE}),
    "retry": leased_update(
        {"$set": {"status": "pending", "available_at": NOW, "last_error": "RuntimeError"}, "$unset": RELEASE}
    ),
    "leased update with explicit defaults": leased_update({"$set": {"updated_at": NOW}}, multi=False, upsert=False),
    "leased update on dead_letters": leased_update({"$set": {"updated_at": NOW}}, collection="dead_letters"),
    "delete leased": leased_delete(),
    "delete leased dead letter": leased_delete(collection="dead_letters"),
    "orphan dead letter": orphan_letter(),
    "invalid-org dead letter": orphan_letter(reason="invalid_org_id", org_id={"$ne": "x"}),
    "dead letter with its org": {
        "insert": "dead_letters",
        "documents": [{"_id": "j", "org_id": "org_alpha", "status": "dead", "reason": "exhausted"}],
    },
}


@pytest.mark.parametrize("command", ALLOWED.values(), ids=ALLOWED.keys())
def test_the_worker_queue_commands_pass(command: dict[str, Any]) -> None:
    assert find_violation(next(iter(command)), command, DB) is None


# --- anything else unscoped on the queue collections is still flagged ----------------------------

FLAGGED: dict[str, dict[str, Any]] = {
    "claim on dead_letters": claim(findAndModify="dead_letters"),
    "claim on another tenant collection": claim(findAndModify="hires"),
    "claim aimed at an org": claim(query={**CLAIM_QUERY, "org_id": {"$ne": "org_alpha"}}),
    "claim by id": claim(query={"_id": "job-1"}),
    "claim searching payloads": claim(query={"payload.hire_id": "h1"}),
    "claim with $nor": claim(query={"$nor": [{"status": "done"}]}),
    "claim with $where": claim(query={"$where": "true"}),
    "claim moving org": claim(update={"$set": {"org_id": "org_beta"}}),
    "claim rewriting payload": claim(update={"$set": {"payload": {}}}),
    "claim rewriting consumer": claim(update={"$set": {"consumer": "other.c"}}),
    "claim renaming": claim(update={"$rename": {"status": "org_id"}}),
    "claim inc other field": claim(update={"$inc": {"max_attempts": 1}}),
    "claim replacement": claim(update={"status": "running"}),
    "claim pipeline update": claim(update=[{"$set": {"status": "running"}}]),
    "claim remove": {"findAndModify": "jobs", "query": CLAIM_QUERY, "remove": True},
    "claim upsert": claim(upsert=True),
    "update by id only": {"update": "jobs", "updates": [{"q": {"_id": "job-1"}, "u": {"$set": {"status": "x"}}}]},
    "update leased plus extra": {
        "update": "jobs",
        "updates": [{"q": {**LEASED, "status": "running"}, "u": {"$set": {"status": "done"}}}],
    },
    "update leased with operator id": {
        "update": "jobs",
        "updates": [{"q": {"_id": {"$ne": None}, "lease_token": "t"}, "u": {"$set": {"status": "done"}}}],
    },
    "update leased with empty token": {
        "update": "jobs",
        "updates": [{"q": {"_id": "j", "lease_token": ""}, "u": {"$set": {"status": "done"}}}],
    },
    "update leased multi": leased_update({"$set": {"status": "done"}}, multi=True),
    "update leased upsert": leased_update({"$set": {"status": "done"}}, upsert=True),
    "update leased moving org": leased_update({"$set": {"org_id": "org_beta"}}),
    "update leased rewriting payload": leased_update({"$set": {"payload": {}}}),
    "update leased replacement": leased_update({"status": "done"}),
    "update leased one of two unscoped": {
        "update": "jobs",
        "updates": [
            {"q": LEASED, "u": {"$set": {"status": "done"}}},
            {"q": {}, "u": {"$set": {"status": "done"}}, "multi": True},
        ],
    },
    "update unscoped filter on jobs": {"update": "jobs", "updates": [{"q": {}, "u": {"$set": {"status": "x"}}}]},
    "delete by id only": {"delete": "jobs", "deletes": [{"q": {"_id": "job-1"}, "limit": 1}]},
    "delete leased many": leased_delete(limit=0),
    "delete everything": {"delete": "dead_letters", "deletes": [{"q": {}, "limit": 0}]},
    "find on dead_letters": {"find": "dead_letters", "filter": {}, "limit": 10},
    "find on jobs": {"find": "jobs", "filter": {"status": "pending"}},
    "count jobs": {"count": "jobs", "query": {}},
    "aggregate jobs": {"aggregate": "jobs", "pipeline": [{"$group": {"_id": "$org_id"}}]},
    "distinct org ids": {"distinct": "jobs", "key": "org_id", "query": {}},
    "insert job without org": {"insert": "jobs", "documents": [{"_id": "j", "consumer": "c.x"}]},
    "dead letter without org for another reason": orphan_letter(reason="exhausted"),
    "dead letter without org not dead": orphan_letter(status="pending"),
    "dead letter in jobs": {**orphan_letter(), "insert": "jobs"},
    "dead letter with a bad reason type": orphan_letter(reason=["missing_org_id"]),
    "claim with an empty filter": claim(query={}),
    "claim with a malformed $or": claim(query={"$or": {"status": "pending"}}),
    "dead letter that is not a document": {"insert": "dead_letters", "documents": [1]},
    "malformed index spec on jobs": {"createIndexes": "jobs", "indexes": [1]},
}


@pytest.mark.parametrize("command", FLAGGED.values(), ids=FLAGGED.keys())
def test_every_other_unscoped_queue_command_is_flagged(command: dict[str, Any]) -> None:
    assert find_violation(next(iter(command)), command, DB) is not None


# --- indexes (ADR 0002 §3) -----------------------------------------------------------------------


def test_a_non_unique_queue_index_may_lead_with_queue_state() -> None:
    key = {"status": 1, "consumer": 1, "available_at": 1}
    assert find_index_violation("jobs", key, unique=False) is None
    assert find_index_violation("dead_letters", key, unique=False) is None


def test_a_unique_queue_index_must_still_start_with_org_id() -> None:
    assert find_index_violation("jobs", {"idempotency_key": 1}, unique=True) is not None
    assert find_index_violation("jobs", {"org_id": 1, "idempotency_key": 1}, unique=True) is None


def test_other_tenant_collections_keep_the_org_prefix_rule() -> None:
    assert find_index_violation("hires", {"status": 1}, unique=False) is not None
    assert find_index_violation("events", {"status": 1}) is not None


def test_create_indexes_on_the_queue_follows_the_same_rule() -> None:
    ok = {"createIndexes": "jobs", "indexes": [{"key": {"status": 1, "available_at": 1}, "name": "claim"}]}
    bad = {"createIndexes": "jobs", "indexes": [{"key": {"lease_token": 1}, "name": "u", "unique": True}]}
    assert find_violation("createIndexes", ok, DB) is None
    assert find_violation("createIndexes", bad, DB) is not None
