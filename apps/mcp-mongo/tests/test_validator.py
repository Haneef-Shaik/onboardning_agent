"""The recursive query validator (MCP-2, PRD §7.5, §8.1): only allowlisted operators and stages,
nothing that runs code or reaches another collection, `pii.*` read-only, `org_id` never named.
"""

from __future__ import annotations

import contextlib
import copy
from typing import Any

import pytest
from onb_mcp_mongo.policy import ALLOWED_STAGES, ToolRejectedError
from onb_mcp_mongo.validator import (
    ACCUMULATOR_OPERATORS,
    EXPRESSION_OPERATORS,
    FORBIDDEN_OPERATORS,
    MAX_DEPTH,
    MAX_STRING_LENGTH,
    QUERY_OPERATORS,
    REASONS,
    UPDATE_OPERATORS,
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


def test_the_forbidden_operators_are_exactly_the_prd_list() -> None:
    prd = {"$where", "$function", "$accumulator", "$lookup", "$graphLookup", "$out", "$merge", "$unionWith"}
    assert prd == FORBIDDEN_OPERATORS


@pytest.mark.parametrize("operator", sorted(FORBIDDEN_OPERATORS))
def test_no_forbidden_operator_is_on_any_allowlist(operator: str) -> None:
    allowlists = QUERY_OPERATORS | EXPRESSION_OPERATORS | ACCUMULATOR_OPERATORS | UPDATE_OPERATORS | ALLOWED_STAGES
    assert operator not in allowlists


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
    for _, tool, args, _ in ACCEPTED + CORPUS:
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


# --- contexts and caps (review fixes) -----------------------------------------------------------


def _filter(value: dict[str, Any]) -> dict[str, Any]:
    return {"collection": R, "filter": value, "limit": 1}


def _pipeline(*stages: dict[str, Any]) -> dict[str, Any]:
    return {"collection": R, "pipeline": list(stages)}


def _update(update: dict[str, Any]) -> dict[str, Any]:
    return {"collection": W, "filter": {"a": 1}, "update": update}


@pytest.mark.parametrize(
    ("tool", "args", "reason"),
    [
        ("find", _filter({"$gt": 1}), "operator_not_allowed"),  # field operator at the query root
        ("find", _filter({"$oid": "0" * 24}), "operator_not_allowed"),  # ObjectId only as a value
        ("find", _filter({"a": {"$and": [{"b": 1}]}}), "operator_not_allowed"),  # logical under a field
        ("find", _filter({"a": {"$gt": 1, "b": 2}}), "invalid_query"),  # operators mixed with fields
        ("find", _filter({"$or": {"a": 1}}), "invalid_query"),  # $or needs a list
        ("find", _filter({"$or": []}), "invalid_query"),
        ("find", _filter({"a": {"$set": 1}}), "operator_not_allowed"),  # update operator in a query
        ("aggregate", _pipeline({"$match": {"a": {"$sum": 1}}}), "operator_not_allowed"),
        ("aggregate", _pipeline({"$project": {"r": "$$USER_ROLES"}}), "variable_not_allowed"),
        ("aggregate", _pipeline({"$project": {"o": "$org_id"}}), "org_id_not_allowed"),
        ("aggregate", _pipeline({"$group": {"_id": "$org_id"}}), "org_id_not_allowed"),
        ("find", _filter({"$expr": {"$eq": ["$$ROOT.org_id", "org_b"]}}), "org_id_not_allowed"),
        ("aggregate", _pipeline({"$count": "a.b"}), "invalid_field_name"),
        ("aggregate", _pipeline({"$group": {"_id": None, "a.b": {"$sum": 1}}}), "invalid_field_name"),
        ("aggregate", _pipeline({"$limit": 2**70}), "too_large"),
        ("insert_one", {"collection": W, "document": {"a": 2**70}}, "too_large"),
        ("insert_one", {"collection": W, "document": {"a": "x" * (MAX_STRING_LENGTH + 1)}}, "too_large"),
        ("aggregate", _pipeline({"$sort": {f"k{i}": 1 for i in range(20_000)}}), "too_large"),
        ("update_one", _update({"$set": {"$[].pii": 1}}), "invalid_field_name"),  # positional first
        ("update_one", _update({"$rename": {"a": "$"}}), "invalid_field_name"),
        ("update_one", _update({"$set": {"_id": 1}}), "id_not_allowed"),
        ("update_one", _update({"$push": {"log": {"$each": [1], "$where": "x"}}}), "forbidden_operator"),
        ("update_one", _update({"$push": {"log": {"$each": [1], "$slice": "x"}}}), "invalid_update"),
    ],
)
def test_context_rules_and_caps(tool: str, args: dict[str, Any], reason: str) -> None:
    assert _reason(tool, args) == reason


def _maps(levels: int) -> Any:
    node: Any = "$arr"
    for _ in range(levels):
        node = {"$map": {"input": "$arr", "as": "x", "in": node}}
    return node


def test_array_iterators_nest_at_most_two_deep() -> None:
    check_call("aggregate", _pipeline({"$project": {"m": _maps(2)}}))
    assert _reason("aggregate", _pipeline({"$project": {"m": _maps(3)}})) == "too_deep"
    assert _reason("count", {"collection": R, "filter": {"$expr": {"$gt": [{"$size": _maps(3)}, 0]}}}) == "too_deep"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("aggregate", _pipeline({"$project": {"n": {"$sum": "$tasks.points"}, "d": {"$dayOfWeek": "$start"}}})),
        ("aggregate", _pipeline({"$addFields": {"all": {"$concatArrays": ["$a", "$b"]}}})),
        ("aggregate", _pipeline({"$project": {"r": "$$ROOT", "now": "$$NOW"}})),
        ("aggregate", _pipeline({"$project": {"f": {"$filter": {"input": "$t", "as": "t", "cond": "$$t.done"}}}})),
        ("update_one", _update({"$push": {"log": {"$each": ["x"], "$slice": -50, "$sort": {"at": -1}}}})),
        ("update_one", _update({"$pull": {"items": {"done": True, "n": {"$lt": 3}}}})),
        ("find", _filter({"tags": {"$elemMatch": {"k": "laptop", "$or": [{"v": 1}, {"v": 2}]}}})),
    ],
)
def test_common_read_and_write_shapes_are_accepted(tool: str, args: dict[str, Any]) -> None:
    check_call(tool, args)
