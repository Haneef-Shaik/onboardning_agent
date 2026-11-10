"""The recursive query validator (PRD §7.5, §8.1): it runs on every call before any policy or
database work.

It walks every argument and allows a `$` operator only where its allowlist says it may appear:
- query operators in filters, `$match` and `$pull`;
- expression operators under `$expr` and in `$project`, `$addFields` and `$group`;
- accumulators as `$group` fields;
- update operators at the top of an update;
- the PRD stages in a pipeline.

Literal values (inserted documents, `$set` values) may carry no operator at all, except an
ObjectId written `{"$oid": "<24 hex>"}`. The PRD's forbidden operators get their own reason
wherever they appear. Field names must be canonical (NFKC), visible and free of `$` segments.
`pii.*` is never written, `org_id` is never named, and depth and size are capped.

A rejection is a `ToolRejectedError` with a stable reason. The message names the argument and,
for a forbidden operator, that operator, which is a fixed name. It never repeats agent text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any

from .policy import ALLOWED_STAGES, ORG_FIELD, ToolRejectedError

__all__ = [
    "ACCUMULATOR_OPERATORS",
    "EXPRESSION_OPERATORS",
    "FORBIDDEN_OPERATORS",
    "MAX_DEPTH",
    "MAX_NAME_LENGTH",
    "MAX_NODES",
    "PII_FIELD",
    "QUERY_OPERATORS",
    "REASONS",
    "UPDATE_OPERATORS",
    "check_call",
]

# PRD §7.5: rejected wherever they appear. Each runs code or reaches another collection.
FORBIDDEN_OPERATORS = frozenset(
    {"$where", "$function", "$accumulator", "$lookup", "$graphLookup", "$out", "$merge", "$unionWith"}
)
QUERY_OPERATORS = frozenset(
    {
        *("$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin"),
        *("$and", "$or", "$nor", "$not"),
        *("$exists", "$type", "$all", "$size", "$elemMatch", "$mod"),
        "$expr",
    }
)
EXPRESSION_OPERATORS = frozenset(
    {
        *("$add", "$subtract", "$multiply", "$divide", "$mod", "$abs", "$ceil", "$floor", "$round", "$trunc"),
        *("$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$cmp", "$and", "$or", "$not"),
        *("$cond", "$ifNull", "$switch"),
        *("$concat", "$toLower", "$toUpper", "$substrCP", "$strLenCP", "$trim", "$split"),
        *("$size", "$arrayElemAt", "$in", "$isArray", "$filter", "$map", "$first", "$last", "$slice"),
        *("$year", "$month", "$dayOfMonth", "$dateToString", "$dateDiff", "$dateAdd"),
        *("$toString", "$toInt", "$toDouble", "$toDate", "$toBool", "$type", "$literal"),
    }
)
ACCUMULATOR_OPERATORS = frozenset({"$sum", "$avg", "$min", "$max", "$first", "$last", "$push", "$addToSet", "$count"})
UPDATE_OPERATORS = frozenset(
    {"$set", "$unset", "$inc", "$mul", "$min", "$max", "$push", "$addToSet", "$pull", "$currentDate", "$rename"}
)

PII_FIELD = "pii"
MAX_DEPTH = 32
MAX_NODES = 10_000
MAX_NAME_LENGTH = 256
_POSITIONAL = frozenset({"$", "$[]"})
_STAGE_LIKE = frozenset({"$lookup", "$graphLookup", "$out", "$merge", "$unionWith"})
_HEX24 = re.compile(r"[0-9a-fA-F]{24}")

REASONS = frozenset(
    {
        "forbidden_operator",
        "operator_not_allowed",
        "stage_not_allowed",
        "invalid_stage",
        "invalid_field_name",
        "non_canonical_name",
        "org_id_not_allowed",
        "pii_read_only",
        "invalid_update",
        "too_deep",
        "too_large",
        "unknown_tool",
    }
)

_MESSAGES = {
    "operator_not_allowed": "{arg} uses an operator that is not allowed there",
    "stage_not_allowed": "{arg} has a stage that is not on the allowlist",
    "invalid_stage": "{arg} has a malformed stage",
    "invalid_field_name": "{arg} has an invalid field name",
    "non_canonical_name": "{arg} has a field name that is not in canonical (NFKC) form",
    "org_id_not_allowed": "{arg} may not name org_id: tenant scope comes from the hub",
    "pii_read_only": "{arg} may not write pii.*: PII token fields are read-only",
    "invalid_update": "{arg} must use update operators such as $set",
    "too_deep": "{arg} is nested too deeply",
    "too_large": "{arg} is too large",
}


def _reject(reason: str, argument: str, operator: str | None = None) -> ToolRejectedError:
    if reason == "forbidden_operator":
        kind = "stage" if argument == "pipeline" and operator in _STAGE_LIKE else "operator"
        return ToolRejectedError(reason, f"{argument} uses the {operator} {kind}, which is not allowed")
    return ToolRejectedError(reason, _MESSAGES[reason].format(arg=argument))


class _Walk:
    """One argument's walk: counts nodes and knows the argument's name for error messages."""

    def __init__(self, argument: str) -> None:
        self.argument = argument
        self.nodes = 0

    def fail(self, reason: str, operator: str | None = None) -> ToolRejectedError:
        return _reject(reason, self.argument, operator)

    def enter(self, depth: int) -> None:
        """Count one node at `depth` (the argument itself is depth 1; leaves count too)."""
        self.nodes += 1
        if depth > MAX_DEPTH:
            raise self.fail("too_deep")
        if self.nodes > MAX_NODES:
            raise self.fail("too_large")

    # --- names ----------------------------------------------------------------------------------

    def visible(self, name: str) -> None:
        if not name or len(name) > MAX_NAME_LENGTH or name != name.strip():
            raise self.fail("invalid_field_name")
        if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn"} for ch in name):
            raise self.fail("invalid_field_name")
        if unicodedata.normalize("NFKC", name) != name:
            raise self.fail("non_canonical_name")

    def operator(self, name: str, allowed: frozenset[str]) -> None:
        """`name` sits where an operator may: it must be on `allowed`."""
        self.visible(name)
        if name in FORBIDDEN_OPERATORS:
            raise self.fail("forbidden_operator", name)
        if name not in allowed:
            raise self.fail("operator_not_allowed")

    def field(self, name: str, *, positional: bool = False) -> list[str]:
        """`name` is a field path: dotted segments, none empty or `$`-led (bar update positionals)."""
        self.visible(name)
        segments = name.split(".")
        for segment in segments:
            if segment in FORBIDDEN_OPERATORS:
                raise self.fail("forbidden_operator", segment)
            if not segment or (segment.startswith("$") and not (positional and segment in _POSITIONAL)):
                raise self.fail("invalid_field_name")
        if segments[0] == ORG_FIELD:
            raise self.fail("org_id_not_allowed")
        return segments

    def key(self, name: object, allowed: frozenset[str]) -> bool:
        """A mapping key that is an operator (True, checked against `allowed`) or a field (False)."""
        if not isinstance(name, str):
            raise self.fail("invalid_field_name")
        if name.startswith("$") or unicodedata.normalize("NFKC", name).startswith("$"):
            self.operator(name, allowed)
            return True
        self.field(name)
        return False

    # --- contexts -------------------------------------------------------------------------------

    def literal(self, node: Any, depth: int) -> None:
        """Plain data: no operator anywhere, except the `{"$oid": hex}` ObjectId form."""
        self.enter(depth)
        if isinstance(node, Mapping):
            if _is_object_id(node):
                return
            for name, value in node.items():
                self.key(name, frozenset())
                self.literal(value, depth + 1)
        elif isinstance(node, list | tuple):
            for item in node:
                self.literal(item, depth + 1)

    def query(self, node: Any, depth: int) -> None:
        """A filter: fields map to values or query-operator documents."""
        self.enter(depth)
        if isinstance(node, Mapping):
            if _is_object_id(node):
                return
            for name, value in node.items():
                if self.key(name, QUERY_OPERATORS) and name == "$expr":
                    self.expression(value, depth + 1)
                else:
                    self.query(value, depth + 1)
        elif isinstance(node, list | tuple):
            for item in node:
                self.query(item, depth + 1)

    def expression(self, node: Any, depth: int) -> None:
        """An aggregation expression. Strings such as `"$field"` are field paths, which only read."""
        self.enter(depth)
        if isinstance(node, Mapping):
            if _is_object_id(node):
                return
            for name, value in node.items():
                self.key(name, EXPRESSION_OPERATORS)
                self.expression(value, depth + 1)
        elif isinstance(node, list | tuple):
            for item in node:
                self.expression(item, depth + 1)


def _is_object_id(node: Mapping[str, Any]) -> bool:
    return len(node) == 1 and isinstance(node.get("$oid"), str) and bool(_HEX24.fullmatch(node["$oid"]))


def _mapping(walk: _Walk, node: Any) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise walk.fail("invalid_stage")
    return node


# --- aggregate --------------------------------------------------------------------------------


def _stage_match(walk: _Walk, body: Any, depth: int) -> None:
    walk.query(_mapping(walk, body), depth)


def _stage_fields(walk: _Walk, body: Any, depth: int) -> None:
    """`$project` and `$addFields`: field → 0/1/bool or an expression."""
    for name, value in _mapping(walk, body).items():
        walk.field(name)
        walk.expression(value, depth + 1)


def _stage_group(walk: _Walk, body: Any, depth: int) -> None:
    group = _mapping(walk, body)
    if "_id" not in group:
        raise walk.fail("invalid_stage")
    for name, value in group.items():
        if name == "_id":
            walk.expression(value, depth + 1)
            continue
        walk.field(name)
        accumulator = _mapping(walk, value)
        if len(accumulator) != 1:
            raise walk.fail("invalid_stage")
        operator, argument = next(iter(accumulator.items()))
        walk.operator(operator, ACCUMULATOR_OPERATORS)
        walk.expression(argument, depth + 2)


def _stage_sort(walk: _Walk, body: Any, depth: int) -> None:
    for name, direction in _mapping(walk, body).items():
        walk.field(name)
        if direction not in (1, -1) or isinstance(direction, bool):
            raise walk.fail("invalid_stage")


def _stage_limit(walk: _Walk, body: Any, depth: int) -> None:
    if not isinstance(body, int) or isinstance(body, bool) or body < 1:
        raise walk.fail("invalid_stage")


def _stage_count(walk: _Walk, body: Any, depth: int) -> None:
    if not isinstance(body, str):
        raise walk.fail("invalid_stage")
    walk.field(body)


def _field_path_value(walk: _Walk, value: Any) -> None:
    if not isinstance(value, str) or not value.startswith("$") or value.startswith("$$"):
        raise walk.fail("invalid_stage")
    walk.field(value[1:])


def _stage_unwind(walk: _Walk, body: Any, depth: int) -> None:
    if isinstance(body, str):
        _field_path_value(walk, body)
        return
    options = _mapping(walk, body)
    if not {"path"} <= set(options) <= {"path", "includeArrayIndex", "preserveNullAndEmptyArrays"}:
        raise walk.fail("invalid_stage")
    _field_path_value(walk, options["path"])
    if "includeArrayIndex" in options:
        if not isinstance(options["includeArrayIndex"], str):
            raise walk.fail("invalid_stage")
        walk.field(options["includeArrayIndex"])
    if not isinstance(options.get("preserveNullAndEmptyArrays", False), bool):
        raise walk.fail("invalid_stage")


_STAGES: dict[str, Callable[[_Walk, Any, int], None]] = {
    "$match": _stage_match,
    "$project": _stage_fields,
    "$addFields": _stage_fields,
    "$group": _stage_group,
    "$sort": _stage_sort,
    "$limit": _stage_limit,
    "$count": _stage_count,
    "$unwind": _stage_unwind,
}
if frozenset(_STAGES) != ALLOWED_STAGES:  # one checker per PRD stage
    raise RuntimeError("validator stages differ from policy.ALLOWED_STAGES")


def _check_pipeline(pipeline: Any) -> None:
    walk = _Walk("pipeline")
    if not isinstance(pipeline, list | tuple):
        raise walk.fail("invalid_stage")
    for stage in pipeline:
        walk.enter(1)
        if not isinstance(stage, Mapping) or len(stage) != 1:
            raise walk.fail("invalid_stage")
        name, body = next(iter(stage.items()))
        if not isinstance(name, str) or not name.startswith("$"):
            raise walk.fail("invalid_stage")
        walk.visible(name)
        if name in FORBIDDEN_OPERATORS:
            raise walk.fail("forbidden_operator", name)
        if name not in _STAGES:
            raise walk.fail("stage_not_allowed")
        _STAGES[name](walk, body, 2)


# --- writes -----------------------------------------------------------------------------------


def _writable_path(walk: _Walk, name: object) -> None:
    if not isinstance(name, str):
        raise walk.fail("invalid_field_name")
    if walk.field(name, positional=True)[0] == PII_FIELD:
        raise walk.fail("pii_read_only")


def _push_value(walk: _Walk, value: Any, depth: int) -> None:
    """`$push`/`$addToSet` take a value or `{"$each": [values]}`."""
    if isinstance(value, Mapping) and "$each" in value:
        if set(value) != {"$each"} or not isinstance(value["$each"], list):
            raise walk.fail("operator_not_allowed")
        walk.literal(value["$each"], depth + 1)
    else:
        walk.literal(value, depth)


def _current_date_value(walk: _Walk, value: Any) -> None:
    if value is True or (isinstance(value, Mapping) and value in ({"$type": "date"}, {"$type": "timestamp"})):
        return
    raise walk.fail("invalid_update")


def _check_update(update: Any) -> None:
    walk = _Walk("update")
    if not isinstance(update, Mapping) or not update:
        raise walk.fail("invalid_update")
    for operator, fields in update.items():
        walk.enter(1)
        if not isinstance(operator, str) or not operator.startswith("$"):
            raise walk.fail("invalid_update")
        walk.operator(operator, UPDATE_OPERATORS)
        if not isinstance(fields, Mapping) or not fields:
            raise walk.fail("invalid_update")
        for path, value in fields.items():
            walk.enter(2)
            _writable_path(walk, path)
            if operator == "$rename":
                _writable_path(walk, value)
            elif operator == "$pull":
                walk.query(value, 3)
            elif operator in {"$push", "$addToSet"}:
                _push_value(walk, value, 3)
            elif operator == "$currentDate":
                _current_date_value(walk, value)
            else:
                walk.literal(value, 3)


def _check_document(document: Any) -> None:
    walk = _Walk("document")
    if not isinstance(document, Mapping):
        raise walk.fail("invalid_field_name")
    for name in document:
        _writable_path(walk, name)
    walk.literal(document, 1)


# --- find extras ------------------------------------------------------------------------------


def _check_names(argument: str, names: Any) -> None:
    walk = _Walk(argument)
    for name in names or ():
        walk.enter(1)
        if not isinstance(name, str):
            raise walk.fail("invalid_field_name")
        walk.field(name)


def _check_filter(filter_: Any) -> None:
    _Walk("filter").query(filter_ if filter_ is not None else {}, 1)


def _find(args: Mapping[str, Any]) -> None:
    _check_filter(args.get("filter"))
    _check_names("projection", list(args.get("projection") or {}))
    _check_names("sort", [pair[0] for pair in args.get("sort") or ()])


def _update_one(args: Mapping[str, Any]) -> None:
    _check_filter(args.get("filter"))
    _check_update(args.get("update"))


_TOOLS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "list_collections": lambda args: None,
    "find": _find,
    "count": lambda args: _check_filter(args.get("filter")),
    "aggregate": lambda args: _check_pipeline(args.get("pipeline")),
    "insert_one": lambda args: _check_document(args.get("document")),
    "update_one": _update_one,
}


def check_call(tool: str, args: Mapping[str, Any]) -> None:
    """Raise `ToolRejectedError` unless every query, pipeline, update or document in `args` is
    allowed. `args` are the contract-validated arguments of `tool`; they are never changed."""
    checker = _TOOLS.get(tool)
    if checker is None:
        raise ToolRejectedError("unknown_tool", "no such tool")
    checker(args)
