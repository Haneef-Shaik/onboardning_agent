"""The recursive query validator (MCP-2, PRD §7.5, §8.1): only allowlisted operators and stages,
nothing that runs code or reaches another collection, `pii.*` read-only, `org_id` never named.
"""

from __future__ import annotations

import contextlib
import copy
from typing import Any

import pytest
from onb_mcp_mongo.policy import ToolRejectedError
from onb_mcp_mongo.validator import (
    FORBIDDEN_OPERATORS,
    MAX_DEPTH,
    REASONS,
    check_call,
)

from .corpus import CORPUS, REQUIRED_KINDS, Payload, R, W


def _reason(tool: str, args: dict[str, Any]) -> str:
    with pytest.raises(ToolRejectedError) as caught:
        check_call(tool, args)
    return caught.value.reason


# --- the injection corpus -----------------------------------------------------------------------


def test_the_corpus_has_at_least_40_payloads_with_unique_ids() -> None:
    ids = [payload[0] for payload in CORPUS]
    assert len(ids) >= 40
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("kind", sorted(REQUIRED_KINDS))
def test_the_corpus_covers_every_kind_the_card_names(kind: str) -> None:
    ids = {payload[0] for payload in CORPUS}
    assert set(REQUIRED_KINDS[kind]) <= ids, kind


@pytest.mark.parametrize(("pid", "tool", "args", "reason"), CORPUS, ids=[p[0] for p in CORPUS])
def test_every_corpus_payload_is_rejected_with_its_named_reason(
    pid: str, tool: str, args: dict[str, Any], reason: str
) -> None:
    assert reason in REASONS
    assert _reason(tool, args) == reason


def test_every_rejection_names_its_argument_and_never_echoes_agent_text() -> None:
    marker = "Zq7-agent-text"
    calls: list[tuple[str, dict[str, Any]]] = [
        ("find", {"collection": R, "filter": {f"$wh{marker}": 1}, "limit": 1}),
        ("find", {"collection": R, "filter": {f"a.{marker}..b": 1}, "limit": 1}),
        ("update_one", {"collection": W, "filter": {"a": 1}, "update": {"$set": {f"pii.{marker}": 1}}}),
    ]
    for tool, args in calls:
        with pytest.raises(ToolRejectedError) as caught:
            check_call(tool, args)
        assert marker not in str(caught.value)
        assert any(name in str(caught.value) for name in ("filter", "update", "document", "pipeline"))


@pytest.mark.parametrize("operator", sorted(FORBIDDEN_OPERATORS))
def test_the_forbidden_operators_are_the_prd_list(operator: str) -> None:
    assert operator in {
        "$where",
        "$function",
        "$accumulator",
        "$lookup",
        "$graphLookup",
        "$out",
        "$merge",
        "$unionWith",
    }


def test_the_prd_forbidden_list_is_complete() -> None:
    assert len(FORBIDDEN_OPERATORS) == 8


def test_a_forbidden_operator_is_named_in_the_message() -> None:
    with pytest.raises(ToolRejectedError) as caught:
        check_call("find", {"collection": R, "filter": {"$where": "x"}, "limit": 1})
    assert "$where" in str(caught.value)


# --- what stays allowed -------------------------------------------------------------------------

ACCEPTED: list[Payload] = [
    ("list", "list_collections", {}, ""),
    ("find-empty", "find", {"collection": R, "filter": {}, "limit": 10}, ""),
    ("find-eq", "find", {"collection": R, "filter": {"status": "open"}, "limit": 10}, ""),
    (
        "find-ops",
        "find",
        {
            "collection": R,
            "filter": {
                "start": {"$gte": "2026-01-01", "$lt": "2026-02-01"},
                "status": {"$in": ["open", "blocked"]},
                "$or": [{"team": "eng"}, {"tags": {"$elemMatch": {"$eq": "laptop"}}}],
                "manager": {"$exists": True},
                "deleted": {"$not": {"$eq": True}},
            },
            "projection": {"name": 1, "pii": 1, "_id": 0},
            "sort": [["start", -1], ["profile.name", 1]],
            "limit": 50,
        },
        "",
    ),
    ("find-oid", "find", {"collection": R, "filter": {"_id": {"$oid": "0" * 24}}, "limit": 1}, ""),
    ("find-dollar-value", "find", {"collection": R, "filter": {"note": "$where is text here"}, "limit": 1}, ""),
    ("find-unicode-name", "find", {"collection": R, "filter": {"名前": "ptk_1", "café": 1}, "limit": 1}, ""),
    ("find-pii-read", "find", {"collection": R, "filter": {"pii.mobile": "ptk_m1"}, "limit": 1}, ""),
    (
        "count-expr",
        "count",
        {"collection": R, "filter": {"$expr": {"$gt": [{"$size": "$tasks"}, {"$add": [1, 2]}]}}},
        "",
    ),
    (
        "aggregate-all-stages",
        "aggregate",
        {
            "collection": R,
            "pipeline": [
                {"$match": {"status": {"$ne": "done"}}},
                {"$unwind": {"path": "$tasks", "preserveNullAndEmptyArrays": True}},
                {"$addFields": {"late": {"$cond": {"if": {"$gt": ["$due", "$$NOW"]}, "then": 0, "else": 1}}}},
                {"$group": {"_id": "$team", "n": {"$sum": 1}, "late": {"$sum": "$late"}, "who": {"$push": "$name"}}},
                {"$project": {"team": "$_id", "n": 1, "share": {"$divide": ["$late", "$n"]}}},
                {"$sort": {"n": -1}},
                {"$limit": 5},
            ],
        },
        "",
    ),
    ("aggregate-count", "aggregate", {"collection": R, "pipeline": [{"$unwind": "$tasks"}, {"$count": "n"}]}, ""),
    ("insert", "insert_one", {"collection": W, "document": {"title": "Laptop", "meta": {"by": "it"}, "n": [1]}}, ""),
    (
        "update",
        "update_one",
        {
            "collection": W,
            "filter": {"_id": {"$oid": "0" * 24}},
            "update": {
                "$set": {"state": "done", "notes.last": "ok"},
                "$inc": {"attempts": 1},
                "$push": {"history": {"$each": ["a", "b"]}},
                "$unset": {"draft": ""},
                "$currentDate": {"updated_at": True},
                "$pull": {"tags": {"$in": ["stale"]}},
                "$rename": {"old": "new"},
            },
        },
        "",
    ),
    (
        "update-positional",
        "update_one",
        {"collection": W, "filter": {"items.id": 3}, "update": {"$set": {"items.$.done": True, "all.$[].seen": 1}}},
        "",
    ),
]


@pytest.mark.parametrize(("pid", "tool", "args", "_"), ACCEPTED, ids=[p[0] for p in ACCEPTED])
def test_ordinary_queries_are_accepted(pid: str, tool: str, args: dict[str, Any], _: str) -> None:
    check_call(tool, args)


def test_the_validator_does_not_change_its_input() -> None:
    for _, tool, args, _ in ACCEPTED + CORPUS[:10]:
        before = copy.deepcopy(args)
        with contextlib.suppress(ToolRejectedError):
            check_call(tool, args)
        assert args == before


def test_nesting_up_to_the_cap_is_accepted_and_one_more_level_is_not() -> None:
    def nested(levels: int) -> dict[str, Any]:
        node: dict[str, Any] = {"x": 1}
        for _ in range(levels):
            node = {"a": node}
        return node

    # nested(n) is n + 1 documents deep, and its innermost value is one level further.
    check_call("find", {"collection": R, "filter": nested(MAX_DEPTH - 2), "limit": 1})
    assert _reason("find", {"collection": R, "filter": nested(MAX_DEPTH - 1), "limit": 1}) == "too_deep"


def test_an_unknown_tool_is_rejected() -> None:
    assert _reason("drop_collection", {"collection": R}) == "unknown_tool"


# --- pii.* is read-only, org_id is never set (box 3) --------------------------------------------


def test_an_update_touching_pii_mobile_is_rejected() -> None:
    args = {"collection": W, "filter": {"state": "todo"}, "update": {"$set": {"pii.mobile": "ptk_m2"}}}
    assert _reason("update_one", args) == "pii_read_only"


@pytest.mark.parametrize("operator", ["$set", "$unset", "$inc", "$mul", "$min", "$max", "$push", "$addToSet", "$pull"])
def test_every_update_operator_on_pii_mobile_is_rejected(operator: str) -> None:
    args = {"collection": W, "filter": {"a": 1}, "update": {operator: {"pii.mobile": 1}}}
    assert _reason("update_one", args) == "pii_read_only"


def test_a_set_of_org_id_is_rejected() -> None:
    args = {"collection": W, "filter": {"state": "todo"}, "update": {"$set": {"org_id": "org_b"}}}
    assert _reason("update_one", args) == "org_id_not_allowed"


def test_a_set_of_an_org_id_subfield_is_rejected() -> None:
    args = {"collection": W, "filter": {"state": "todo"}, "update": {"$set": {"org_id.x": "org_b"}}}
    assert _reason("update_one", args) == "org_id_not_allowed"


def test_a_field_merely_named_like_pii_is_not_pii() -> None:
    check_call("update_one", {"collection": W, "filter": {"a": 1}, "update": {"$set": {"piiless": 1, "x.pii": 2}}})


def test_a_value_spelled_org_id_is_data_not_a_field() -> None:
    check_call("find", {"collection": R, "filter": {"label": "org_id"}, "limit": 1})
