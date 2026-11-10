"""The injection corpus (MCP-2, PRD §7.5, §8.1): calls an agent might send to read across tenants,
run code, write outside its collection, or change `org_id` or `pii.*`. Every entry is
`(id, tool, arguments, reason)`, where `reason` is the `denial.reason` the validator must give.

The collections are ones the test context may use, so a payload is rejected for its query and not
because of the collection it names.
"""

from __future__ import annotations

from typing import Any

R = "new_hires"  # readable in the test context
W = "run_tasks"  # writable in the test context

Payload = tuple[str, str, dict[str, Any], str]


def _find(filter_: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"collection": R, "filter": filter_, "limit": 10, **extra}


def _agg(*stages: dict[str, Any]) -> dict[str, Any]:
    return {"collection": R, "pipeline": list(stages)}


def _upd(update: dict[str, Any], filter_: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"collection": W, "filter": filter_ or {"state": "todo"}, "update": update}


def _ins(document: dict[str, Any]) -> dict[str, Any]:
    return {"collection": W, "document": document}


def _nest(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"x": 1}
    for _ in range(depth):
        node = {"$and": [node]}
    return node


JS = "function () { return true; }"

CORPUS: list[Payload] = [
    # --- $where: server-side JavaScript in a query ---------------------------------------------
    ("where-top", "find", _find({"$where": "this.org_id != 'org_a'"}), "forbidden_operator"),
    ("where-in-or", "find", _find({"$or": [{"status": "open"}, {"$where": JS}]}), "forbidden_operator"),
    ("where-in-count", "count", {"collection": R, "filter": {"$where": "sleep(5000) || true"}}, "forbidden_operator"),
    ("where-in-match", "aggregate", _agg({"$match": {"$where": JS}}), "forbidden_operator"),
    ("where-in-update-filter", "update_one", _upd({"$set": {"a": 1}}, {"$where": JS}), "forbidden_operator"),
    ("where-under-elemMatch", "find", _find({"tags": {"$elemMatch": {"$where": JS}}}), "forbidden_operator"),
    ("where-under-not", "find", _find({"a": {"$not": {"$where": JS}}}), "forbidden_operator"),
    ("where-in-pull", "update_one", _upd({"$pull": {"tags": {"$where": JS}}}), "forbidden_operator"),
    # --- $function / $accumulator / $expr with JS ----------------------------------------------
    (
        "expr-function",
        "find",
        _find({"$expr": {"$function": {"body": JS, "args": [], "lang": "js"}}}),
        "forbidden_operator",
    ),
    (
        "expr-function-nested",
        "count",
        {"collection": R, "filter": {"$expr": {"$and": [{"$eq": ["$a", 1]}, {"$function": {"body": JS}}]}}},
        "forbidden_operator",
    ),
    (
        "expr-function-in-match",
        "aggregate",
        _agg({"$match": {"$expr": {"$gt": [{"$function": {"body": JS, "args": [], "lang": "js"}}, 0]}}}),
        "forbidden_operator",
    ),
    (
        "function-in-addFields",
        "aggregate",
        _agg({"$addFields": {"x": {"$function": {"body": JS, "args": [], "lang": "js"}}}}),
        "forbidden_operator",
    ),
    (
        "function-in-project",
        "aggregate",
        _agg({"$project": {"x": {"$function": {"body": JS, "args": [], "lang": "js"}}}}),
        "forbidden_operator",
    ),
    (
        "function-in-group-id",
        "aggregate",
        _agg({"$group": {"_id": {"$function": {"body": JS, "args": [], "lang": "js"}}}}),
        "forbidden_operator",
    ),
    (
        "accumulator-in-group",
        "aggregate",
        _agg({"$group": {"_id": None, "x": {"$accumulator": {"init": JS, "accumulate": JS, "lang": "js"}}}}),
        "forbidden_operator",
    ),
    (
        "accumulator-in-expr",
        "find",
        _find({"$expr": {"$accumulator": {"init": JS}}}),
        "forbidden_operator",
    ),
    (
        "function-in-sum",
        "aggregate",
        _agg({"$group": {"_id": None, "n": {"$sum": {"$function": {}}}}}),
        "forbidden_operator",
    ),
    # --- cross-collection and output stages ----------------------------------------------------
    (
        "lookup",
        "aggregate",
        _agg({"$lookup": {"from": "pii_vault", "localField": "a", "foreignField": "b", "as": "v"}}),
        "forbidden_operator",
    ),
    (
        "lookup-after-match",
        "aggregate",
        _agg({"$match": {}}, {"$lookup": {"from": "orgs", "pipeline": [], "as": "o"}}),
        "forbidden_operator",
    ),
    (
        "graphLookup",
        "aggregate",
        _agg(
            {"$graphLookup": {"from": R, "startWith": "$a", "connectFromField": "a", "connectToField": "b", "as": "g"}}
        ),
        "forbidden_operator",
    ),
    ("out", "aggregate", _agg({"$match": {}}, {"$out": "stolen"}), "forbidden_operator"),
    ("out-db", "aggregate", _agg({"$out": {"db": "admin", "coll": "x"}}), "forbidden_operator"),
    ("merge", "aggregate", _agg({"$merge": {"into": W}}), "forbidden_operator"),
    ("unionWith", "aggregate", _agg({"$unionWith": {"coll": R, "pipeline": []}}), "forbidden_operator"),
    ("unionWith-string", "aggregate", _agg({"$unionWith": "pii_vault"}), "forbidden_operator"),
    ("lookup-as-query-key", "find", _find({"$lookup": {"from": "pii_vault"}}), "forbidden_operator"),
    # --- stages and operators off the allowlist ------------------------------------------------
    ("facet", "aggregate", _agg({"$facet": {"a": [{"$lookup": {"from": "orgs"}}]}}), "stage_not_allowed"),
    ("set-stage", "aggregate", _agg({"$set": {"org_id": "org_b"}}), "stage_not_allowed"),
    ("collStats", "aggregate", _agg({"$collStats": {}}), "stage_not_allowed"),
    ("currentOp", "aggregate", _agg({"$currentOp": {"allUsers": True}}), "stage_not_allowed"),
    ("regex", "find", _find({"name": {"$regex": "^(a+)+$"}}), "operator_not_allowed"),
    ("jsonSchema", "find", _find({"$jsonSchema": {}}), "operator_not_allowed"),
    ("text", "find", _find({"$text": {"$search": "x"}}), "operator_not_allowed"),
    ("comment", "find", _find({"a": 1, "$comment": "x"}), "operator_not_allowed"),
    ("code-ejson", "find", _find({"a": {"$code": JS}}), "operator_not_allowed"),
    ("setOnInsert", "update_one", _upd({"$setOnInsert": {"a": 1}}), "operator_not_allowed"),
    ("bit-update", "update_one", _upd({"$bit": {"a": {"and": 1}}}), "operator_not_allowed"),
    ("operator-in-insert", "insert_one", _ins({"title": {"$where": JS}}), "forbidden_operator"),
    ("unknown-in-insert", "insert_one", _ins({"title": {"$gt": 1}}), "operator_not_allowed"),
    ("operator-in-set-value", "update_one", _upd({"$set": {"a": {"$function": {}}}}), "forbidden_operator"),
    # --- dotted and malformed field paths ------------------------------------------------------
    ("dotted-dollar-segment", "find", _find({"a.$where": JS}), "forbidden_operator"),
    ("dotted-empty-segment", "find", _find({"a..b": 1}), "invalid_field_name"),
    ("leading-dot", "update_one", _upd({"$set": {".a": 1}}), "invalid_field_name"),
    ("projection-operator", "find", _find({}, projection={"$where": 1}), "forbidden_operator"),
    ("sort-operator-key", "find", _find({}, sort=[["$natural", 1]]), "invalid_field_name"),
    ("nul-in-name", "find", _find({"a\x00b": 1}), "invalid_field_name"),
    # --- unicode -------------------------------------------------------------------------------
    ("fullwidth-dollar", "find", _find({"\uff04where": JS}), "non_canonical_name"),
    ("small-dollar", "find", _find({"\ufe69where": JS}), "non_canonical_name"),
    ("cyrillic-e-where", "find", _find({"$wh\u0435re": JS}), "operator_not_allowed"),
    ("zero-width-in-op", "find", _find({"$wh\u200bere": JS}), "invalid_field_name"),
    ("fullwidth-org-id", "find", _find({"\uff4frg_id": "org_b"}), "non_canonical_name"),
    ("rtl-override", "update_one", _upd({"$set": {"a\u202eb": 1}}), "invalid_field_name"),
    # --- tenant scope --------------------------------------------------------------------------
    ("org-in-filter", "find", _find({"org_id": "org_b"}), "org_id_not_allowed"),
    ("org-dotted", "count", {"collection": R, "filter": {"org_id.x": 1}}, "org_id_not_allowed"),
    ("org-nested-or", "find", _find({"$or": [{"a": 1}, {"org_id": {"$ne": "org_a"}}]}), "org_id_not_allowed"),
    ("org-set", "update_one", _upd({"$set": {"org_id": "org_b"}}), "org_id_not_allowed"),
    ("org-unset", "update_one", _upd({"$unset": {"org_id": ""}}), "org_id_not_allowed"),
    ("org-rename-target", "update_one", _upd({"$rename": {"x": "org_id"}}), "org_id_not_allowed"),
    ("org-insert", "insert_one", _ins({"org_id": "org_b", "title": "x"}), "org_id_not_allowed"),
    ("org-addFields", "aggregate", _agg({"$addFields": {"org_id": "org_b"}}), "org_id_not_allowed"),
    (
        "org-group-output",
        "aggregate",
        _agg({"$group": {"_id": None, "org_id": {"$first": "$a"}}}),
        "org_id_not_allowed",
    ),
    ("org-count-name", "aggregate", _agg({"$count": "org_id"}), "org_id_not_allowed"),
    # --- pii.* is read-only --------------------------------------------------------------------
    ("pii-set-mobile", "update_one", _upd({"$set": {"pii.mobile": "ptk_x"}}), "pii_read_only"),
    ("pii-set-whole", "update_one", _upd({"$set": {"pii": {"mobile": "ptk_x"}}}), "pii_read_only"),
    ("pii-unset", "update_one", _upd({"$unset": {"pii.mobile": ""}}), "pii_read_only"),
    ("pii-rename-from", "update_one", _upd({"$rename": {"pii.mobile": "m"}}), "pii_read_only"),
    ("pii-rename-to", "update_one", _upd({"$rename": {"m": "pii.mobile"}}), "pii_read_only"),
    ("pii-push", "update_one", _upd({"$push": {"pii.emails": "ptk_y"}}), "pii_read_only"),
    ("pii-insert", "insert_one", _ins({"title": "x", "pii": {"pan": "ptk_z"}}), "pii_read_only"),
    ("pii-insert-dotted", "insert_one", _ins({"pii.pan": "ptk_z"}), "pii_read_only"),
    # --- size and depth ------------------------------------------------------------------------
    ("deep-nesting", "find", _find(_nest(60)), "too_deep"),
    ("wide", "find", _find({"$or": [{"a": i} for i in range(20_000)]}), "too_large"),
]

# What the card asks the corpus to cover, and the ids that cover it.
REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "nested": ("where-in-or", "expr-function-nested", "org-nested-or", "deep-nesting"),
    "dotted": ("dotted-dollar-segment", "org-dotted", "pii-set-mobile"),
    "unicode": ("fullwidth-dollar", "cyrillic-e-where", "zero-width-in-op"),
    "$where": ("where-top",),
    "$function": ("expr-function",),
    "$accumulator": ("accumulator-in-group",),
    "$expr with JS": ("expr-function", "expr-function-in-match"),
    "$lookup": ("lookup",),
    "$graphLookup": ("graphLookup",),
    "$out": ("out",),
    "$merge": ("merge",),
    "$unionWith": ("unionWith",),
}
