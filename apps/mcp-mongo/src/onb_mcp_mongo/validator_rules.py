"""Allowlists and the recursive walker behind `validator.check_call` (PRD §7.5, §8.1).

A `Walk` checks one argument (`filter`, `pipeline`, `update`, `document`, ...) in one of four
contexts:
- query document: field names, plus `$and`/`$or`/`$nor`/`$expr`;
- field condition: comparison and array operators such as `$gt`, `$in` and `$elemMatch`;
- expression: aggregation operators; `"$path"` strings are field paths, `"$$NAME"` variables;
- literal: plain data, with no operator except the ObjectId form `{"$oid": "<24 hex>"}`.

Every node counts towards `MAX_NODES` and `MAX_DEPTH`. Integers must fit in 64 bits and strings
in `MAX_STRING_LENGTH`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .policy import ORG_FIELD, ToolRejectedError

# PRD §7.5: rejected wherever they appear. Each runs code or reaches another collection.
FORBIDDEN_OPERATORS = frozenset(
    {"$where", "$function", "$accumulator", "$lookup", "$graphLookup", "$out", "$merge", "$unionWith"}
)
QUERY_DOCUMENT_OPERATORS = frozenset({"$and", "$or", "$nor", "$expr"})
# `$regex`, `$text` and `$regexMatch` are left out on purpose: patterns can be catastrophic
# (ReDoS), and search by token makes them unnecessary.
FIELD_OPERATORS = frozenset(
    {
        *("$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin"),
        *("$not", "$exists", "$type", "$all", "$size", "$elemMatch", "$mod"),
    }
)
QUERY_OPERATORS = QUERY_DOCUMENT_OPERATORS | FIELD_OPERATORS
ARRAY_ITERATORS = frozenset({"$map", "$filter"})
EXPRESSION_OPERATORS = frozenset(
    {
        *("$add", "$subtract", "$multiply", "$divide", "$mod", "$abs", "$ceil", "$floor", "$round", "$trunc"),
        *("$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$cmp", "$and", "$or", "$not"),
        *("$cond", "$ifNull", "$switch"),
        *("$concat", "$toLower", "$toUpper", "$substrCP", "$strLenCP", "$trim", "$split"),
        *("$size", "$arrayElemAt", "$in", "$isArray", "$concatArrays", "$first", "$last", "$slice"),
        *ARRAY_ITERATORS,
        *("$sum", "$avg", "$min", "$max"),
        *("$year", "$month", "$week", "$dayOfMonth", "$dayOfWeek", "$dayOfYear", "$hour", "$minute"),
        *("$dateToString", "$dateDiff", "$dateAdd", "$dateSubtract"),
        *("$toString", "$toInt", "$toDouble", "$toDate", "$toBool", "$type", "$literal"),
    }
)
ACCUMULATOR_OPERATORS = frozenset({"$sum", "$avg", "$min", "$max", "$first", "$last", "$push", "$addToSet", "$count"})
UPDATE_OPERATORS = frozenset(
    {"$set", "$unset", "$inc", "$mul", "$min", "$max", "$push", "$addToSet", "$pull", "$currentDate", "$rename"}
)
# System variables an expression may read; `$$USER_ROLES` and the like would leak server state.
# User variables (`as` names) start with a lower-case letter, so they can never shadow these.
SYSTEM_VARIABLES = frozenset({"ROOT", "CURRENT", "REMOVE", "NOW"})

PII_FIELD = "pii"
ID_FIELD = "_id"
MAX_DEPTH = 32
MAX_NODES = 10_000
MAX_NAME_LENGTH = 256
MAX_STRING_LENGTH = 65_536
MAX_ARRAY_ITERATOR_NESTING = 2
_INT64 = 2**63
POSITIONAL = frozenset({"$", "$[]"})
_STAGE_LIKE = frozenset({"$lookup", "$graphLookup", "$out", "$merge", "$unionWith"})
_HEX24 = re.compile(r"[0-9a-fA-F]{24}")
_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

_MESSAGES = {
    "operator_not_allowed": "{arg} uses an operator that is not allowed there",
    "stage_not_allowed": "{arg} has a stage that is not on the allowlist",
    "invalid_stage": "{arg} has a malformed stage",
    "invalid_query": "{arg} is not a well-formed query",
    "invalid_field_name": "{arg} has an invalid field name",
    "non_canonical_name": "{arg} has a field name that is not in canonical (NFKC) form",
    "variable_not_allowed": "{arg} reads a system variable that is not allowed",
    "org_id_not_allowed": "{arg} may not name org_id: tenant scope comes from the hub",
    "pii_read_only": "{arg} may not write pii.*: PII token fields are read-only",
    "id_not_allowed": "{arg} may not set _id",
    "invalid_update": "{arg} must use update operators such as $set",
    "too_deep": "{arg} is nested too deeply",
    "too_large": "{arg} is too large",
}
REASONS = frozenset({*_MESSAGES, "forbidden_operator", "unknown_tool"})


def is_object_id(node: Any) -> bool:
    return (
        isinstance(node, Mapping)
        and len(node) == 1
        and isinstance(node.get("$oid"), str)
        and bool(_HEX24.fullmatch(node["$oid"]))
    )


def _looks_like_operator(name: str) -> bool:
    return name.startswith("$") or unicodedata.normalize("NFKC", name).startswith("$")


class Walk:
    """One argument's walk: counts nodes and knows the argument's name for error messages."""

    def __init__(self, argument: str) -> None:
        self.argument = argument
        self.nodes = 0
        self.iterators = 0  # `$map`/`$filter` currently open

    def fail(self, reason: str, operator: str | None = None) -> ToolRejectedError:
        if reason == "forbidden_operator":
            kind = "stage" if self.argument == "pipeline" and operator in _STAGE_LIKE else "operator"
            return ToolRejectedError(reason, f"{self.argument} uses the {operator} {kind}, which is not allowed")
        return ToolRejectedError(reason, _MESSAGES[reason].format(arg=self.argument))

    def enter(self, depth: int, node: Any = None) -> None:
        """Count one node at `depth` (the argument itself is depth 1; leaves count too)."""
        self.nodes += 1
        if depth > MAX_DEPTH:
            raise self.fail("too_deep")
        too_big_int = isinstance(node, int) and not -_INT64 <= node < _INT64
        too_long = isinstance(node, str) and len(node) > MAX_STRING_LENGTH
        if self.nodes > MAX_NODES or too_big_int or too_long:
            raise self.fail("too_large")

    # --- names ----------------------------------------------------------------------------------

    def visible(self, name: str) -> None:
        if not name or len(name) > MAX_NAME_LENGTH or name != name.strip():
            raise self.fail("invalid_field_name")
        if any(unicodedata.category(ch) in _INVISIBLE for ch in name):
            raise self.fail("invalid_field_name")
        if unicodedata.normalize("NFKC", name) != name:
            raise self.fail("non_canonical_name")

    def operator(self, name: object, allowed: frozenset[str]) -> str:
        """`name` sits where only an operator may: it must be on `allowed`."""
        if not isinstance(name, str) or not _looks_like_operator(name):
            raise self.fail("invalid_query")
        self.visible(name)
        if name in FORBIDDEN_OPERATORS:
            raise self.fail("forbidden_operator", name)
        if name not in allowed:
            raise self.fail("operator_not_allowed")
        return name

    def field(self, name: object, *, positional: bool = False) -> list[str]:
        """A field path: dotted segments, none empty or `$`-led (bar update positionals after the
        first segment). `org_id` is never named as a field, at any depth."""
        if not isinstance(name, str):
            raise self.fail("invalid_field_name")
        self.visible(name)
        segments = name.split(".")
        for index, segment in enumerate(segments):
            if segment in FORBIDDEN_OPERATORS:
                raise self.fail("forbidden_operator", segment)
            allowed_positional = positional and index > 0 and segment in POSITIONAL
            if not segment or (segment.startswith("$") and not allowed_positional):
                raise self.fail("invalid_field_name")
        if segments[0] == ORG_FIELD:
            raise self.fail("org_id_not_allowed")
        return segments

    def output_name(self, name: object) -> None:
        """A field a stage creates (`$count`, `$group` outputs): one segment, no dots."""
        if isinstance(name, str) and "." in name:
            raise self.fail("invalid_field_name")
        self.field(name)

    def key(self, name: object, allowed: frozenset[str]) -> bool:
        """A key that is an operator (True, checked against `allowed`) or a field (False)."""
        if isinstance(name, str) and _looks_like_operator(name):
            self.operator(name, allowed)
            return True
        self.field(name)
        return False

    # --- contexts -------------------------------------------------------------------------------

    def literal(self, node: Any, depth: int) -> None:
        """Plain data: no operator anywhere, except the `{"$oid": hex}` ObjectId form."""
        self.enter(depth, node)
        if is_object_id(node):
            return
        if isinstance(node, Mapping):
            for name, value in node.items():
                self.key(name, frozenset())
                self.literal(value, depth + 1)
        elif isinstance(node, list | tuple):
            for item in node:
                self.literal(item, depth + 1)

    def query(self, node: Any, depth: int) -> None:
        """A query document: fields → conditions, or `$and`/`$or`/`$nor` lists, or `$expr`."""
        self.enter(depth, node)
        if not isinstance(node, Mapping):
            raise self.fail("invalid_query")
        for name, value in node.items():
            if not self.key(name, QUERY_DOCUMENT_OPERATORS):
                self.condition(value, depth + 1)
            elif name == "$expr":
                self.expression(value, depth + 1)
            else:
                self._clauses(value, depth + 1)

    def _clauses(self, node: Any, depth: int) -> None:
        self.enter(depth, node)
        if not isinstance(node, list | tuple) or not node:
            raise self.fail("invalid_query")
        for clause in node:
            self.query(clause, depth + 1)

    def condition(self, node: Any, depth: int) -> None:
        """A field's value: a literal, or a document of field operators only."""
        if not isinstance(node, Mapping) or is_object_id(node) or not any(map(_is_op_key, node)):
            self.literal(node, depth)
            return
        self.enter(depth, node)
        for name, value in node.items():
            operator = self.operator(name, FIELD_OPERATORS)
            if operator == "$not":
                self.condition(value, depth + 1)
            elif operator == "$elemMatch":
                self.element_match(value, depth + 1)
            else:
                self.literal(value, depth + 1)

    def element_match(self, node: Any, depth: int) -> None:
        """`$elemMatch` (and a `$pull` condition): field operators, or a query on sub-documents."""
        is_conditions = isinstance(node, Mapping) and any(map(_is_op_key, node))
        if is_conditions and not any(name in QUERY_DOCUMENT_OPERATORS for name in node):
            self.condition(node, depth)
        else:
            self.query(node, depth)

    def expression(self, node: Any, depth: int) -> None:
        """An aggregation expression. A `"$path"` string reads a field; `"$$X"` reads a variable."""
        self.enter(depth, node)
        if isinstance(node, str):
            self._path_string(node)
        elif is_object_id(node):
            return
        elif isinstance(node, Mapping):
            for name, value in node.items():
                self._expression_entry(name, value, depth)
        elif isinstance(node, list | tuple):
            for item in node:
                self.expression(item, depth + 1)

    def _expression_entry(self, name: object, value: Any, depth: int) -> None:
        iterator = self.key(name, EXPRESSION_OPERATORS) and name in ARRAY_ITERATORS
        if iterator:
            self.iterators += 1
            if self.iterators > MAX_ARRAY_ITERATOR_NESTING:
                raise self.fail("too_deep")
        try:
            self.expression(value, depth + 1)
        finally:
            if iterator:
                self.iterators -= 1

    def _path_string(self, value: str) -> None:
        if value.startswith("$$"):
            variable, _, rest = value[2:].partition(".")
            if not variable[:1].islower() and variable not in SYSTEM_VARIABLES:
                raise self.fail("variable_not_allowed")
            if rest:
                self.field(rest)
        elif value.startswith("$"):
            self.field(value[1:])


def _is_op_key(name: object) -> bool:
    return isinstance(name, str) and _looks_like_operator(name)
