"""The recursive query validator (PRD §7.5, §8.1): it runs on every call, after the contract and
before any policy or database work (`server.TracedTool`).

Each argument is walked in its context (see `validator_rules`):
- `find`/`count`/`update_one` filters and `$match` are query documents;
- `$project`, `$addFields`, `$group` and `$expr` hold expressions;
- an update is update operators over writable paths;
- an inserted document, and `$set` values, are literals.

The PRD's forbidden operators get their own reason wherever they appear, and any other operator
must be on the allowlist for its context. Field names must be canonical (NFKC), visible and free
of `$` segments. `pii.*` and `_id` are never written. `org_id` is never named, as a field or as a
`"$org_id"` path. Depth, node count, integers and strings are capped.

`policy.py` repeats the `org_id`, stage and update-shape checks after this, as defence in depth.
Both raise the same reasons for the cases they share.

A rejection is a `ToolRejectedError` with a stable reason. The message names the argument and,
for a forbidden operator, that operator, which is a fixed name. It never repeats agent text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .policy import ALLOWED_STAGES, ToolRejectedError
from .validator_rules import (
    ACCUMULATOR_OPERATORS,
    EXPRESSION_OPERATORS,
    FIELD_OPERATORS,
    FORBIDDEN_OPERATORS,
    ID_FIELD,
    MAX_DEPTH,
    MAX_NAME_LENGTH,
    MAX_NODES,
    MAX_STRING_LENGTH,
    PII_FIELD,
    QUERY_DOCUMENT_OPERATORS,
    QUERY_OPERATORS,
    REASONS,
    UPDATE_OPERATORS,
    Walk,
)

__all__ = [
    "ACCUMULATOR_OPERATORS",
    "EXPRESSION_OPERATORS",
    "FIELD_OPERATORS",
    "FORBIDDEN_OPERATORS",
    "MAX_DEPTH",
    "MAX_NAME_LENGTH",
    "MAX_NODES",
    "MAX_STRING_LENGTH",
    "PII_FIELD",
    "QUERY_DOCUMENT_OPERATORS",
    "QUERY_OPERATORS",
    "REASONS",
    "UPDATE_OPERATORS",
    "check_call",
]


def _mapping(walk: Walk, node: Any) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise walk.fail("invalid_stage")
    return node


# --- aggregate --------------------------------------------------------------------------------


def _stage_match(walk: Walk, body: Any, depth: int) -> None:
    walk.query(_mapping(walk, body), depth)


def _stage_fields(walk: Walk, body: Any, depth: int) -> None:
    """`$project` and `$addFields`: field → 0/1/bool or an expression."""
    for name, value in _mapping(walk, body).items():
        walk.field(name)
        walk.expression(value, depth + 1)


def _stage_group(walk: Walk, body: Any, depth: int) -> None:
    group = _mapping(walk, body)
    if "_id" not in group:
        raise walk.fail("invalid_stage")
    for name, value in group.items():
        if name == "_id":
            walk.expression(value, depth + 1)
            continue
        walk.output_name(name)
        accumulator = _mapping(walk, value)
        if len(accumulator) != 1:
            raise walk.fail("invalid_stage")
        operator, argument = next(iter(accumulator.items()))
        walk.enter(depth + 1)
        walk.operator(operator, ACCUMULATOR_OPERATORS)
        walk.expression(argument, depth + 2)


def _stage_sort(walk: Walk, body: Any, depth: int) -> None:
    for name, direction in _mapping(walk, body).items():
        walk.enter(depth + 1)
        walk.field(name)
        if isinstance(direction, bool) or direction not in (1, -1):
            raise walk.fail("invalid_stage")


def _stage_limit(walk: Walk, body: Any, depth: int) -> None:
    walk.enter(depth, body)
    if not isinstance(body, int) or isinstance(body, bool) or body < 1:
        raise walk.fail("invalid_stage")


def _stage_count(walk: Walk, body: Any, depth: int) -> None:
    if not isinstance(body, str):
        raise walk.fail("invalid_stage")
    walk.output_name(body)


def _field_path_value(walk: Walk, value: Any) -> None:
    if not isinstance(value, str) or not value.startswith("$") or value.startswith("$$"):
        raise walk.fail("invalid_stage")
    walk.field(value[1:])


def _stage_unwind(walk: Walk, body: Any, depth: int) -> None:
    if isinstance(body, str):
        _field_path_value(walk, body)
        return
    options = _mapping(walk, body)
    if not {"path"} <= set(options) <= {"path", "includeArrayIndex", "preserveNullAndEmptyArrays"}:
        raise walk.fail("invalid_stage")
    _field_path_value(walk, options["path"])
    if "includeArrayIndex" in options:
        walk.output_name(options["includeArrayIndex"])
    if not isinstance(options.get("preserveNullAndEmptyArrays", False), bool):
        raise walk.fail("invalid_stage")


_STAGES: dict[str, Callable[[Walk, Any, int], None]] = {
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
    walk = Walk("pipeline")
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


def _writable_path(walk: Walk, name: object, *, positional: bool = True) -> None:
    """A path an update or insert writes. Never `pii.*`, `org_id` or `_id`; the first segment is
    never positional, so it is always the top-level field written."""
    first = walk.field(name, positional=positional)[0]
    if first == PII_FIELD:
        raise walk.fail("pii_read_only")
    if first == ID_FIELD:
        raise walk.fail("id_not_allowed")


_PUSH_MODIFIERS = frozenset({"$each", "$slice", "$sort", "$position"})


def _push_value(walk: Walk, value: Any, depth: int) -> None:
    """`$push`/`$addToSet` take a value, or `{"$each": [...]}` with `$slice`/`$sort`/`$position`."""
    if not (isinstance(value, Mapping) and "$each" in value):
        walk.literal(value, depth)
        return
    for modifier in value:
        walk.operator(modifier, _PUSH_MODIFIERS)
    if not isinstance(value["$each"], list):
        raise walk.fail("invalid_update")
    walk.literal(value["$each"], depth + 1)
    for name in ("$slice", "$position"):
        if name in value and (isinstance(value[name], bool) or not isinstance(value[name], int)):
            raise walk.fail("invalid_update")
    if "$sort" in value:
        _push_sort(walk, value["$sort"], depth + 1)


def _push_sort(walk: Walk, spec: Any, depth: int) -> None:
    if spec in (1, -1) and not isinstance(spec, bool):
        return
    if not isinstance(spec, Mapping) or not spec:
        raise walk.fail("invalid_update")
    for name, direction in spec.items():
        walk.enter(depth)
        walk.field(name)
        if isinstance(direction, bool) or direction not in (1, -1):
            raise walk.fail("invalid_update")


def _current_date_value(walk: Walk, value: Any) -> None:
    if value is True or (isinstance(value, Mapping) and value in ({"$type": "date"}, {"$type": "timestamp"})):
        return
    raise walk.fail("invalid_update")


def _update_value(walk: Walk, operator: str, value: Any) -> None:
    if operator == "$rename":
        _writable_path(walk, value, positional=False)
    elif operator == "$pull" and isinstance(value, Mapping):
        walk.element_match(value, 3)
    elif operator in {"$push", "$addToSet"}:
        _push_value(walk, value, 3)
    elif operator == "$currentDate":
        _current_date_value(walk, value)
    else:
        walk.literal(value, 3)


def _check_update(update: Any) -> None:
    walk = Walk("update")
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
            _writable_path(walk, path, positional=operator != "$rename")
            _update_value(walk, operator, value)


def _check_document(document: Any) -> None:
    walk = Walk("document")
    if not isinstance(document, Mapping):
        raise walk.fail("invalid_field_name")
    for name in document:
        if name != ID_FIELD:  # `_id` is policy.reject_id's, with its own reason
            _writable_path(walk, name, positional=False)
    walk.literal(document, 1)


# --- find extras ------------------------------------------------------------------------------


def _check_names(argument: str, names: Any) -> None:
    walk = Walk(argument)
    for name in names or ():
        walk.enter(1)
        walk.field(name)


def _check_filter(filter_: Any) -> None:
    Walk("filter").query(filter_ if filter_ is not None else {}, 1)


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
