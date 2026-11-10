"""Validation errors as client-safe details: where (a declared field path) and what (a fixed text).

Pydantic's `msg` can carry the submitted value (a custom validator's message, a union tag), and its
`loc` can carry client-chosen keys (dict keys, unknown field names). So neither is passed through:
- `issue` comes from a fixed table keyed by the error `type`;
- `field` keeps only segments the route declares, resolved against its body schema; a dict key
  becomes `*`, an unknown field `<extra>`, anything unresolvable `*`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onb_contracts import ErrorDetail
from pydantic import TypeAdapter

_ISSUES: Mapping[str, str] = {
    "missing": "Field required",
    "extra_forbidden": "Unknown field",
    "json_invalid": "Malformed JSON",
    "literal_error": "Not an allowed value",
    "enum": "Not an allowed value",
    "union_tag_invalid": "Not an allowed value",
    "union_tag_not_found": "Not an allowed value",
    "string_pattern_mismatch": "Wrong format",
}
_ISSUE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_type", "Wrong type"),
    ("_parsing", "Wrong type"),
    ("_too_short", "Wrong length"),
    ("_too_long", "Wrong length"),
)
_RANGE_PREFIXES = ("greater_than", "less_than", "multiple_of")
GENERIC_ISSUE = "Invalid value"
WILDCARD = "*"
EXTRA = "<extra>"
_PARAM_LOCATIONS = frozenset({"query", "path", "header", "cookie"})


def issue_for(error_type: str) -> str:
    if error_type in _ISSUES:
        return _ISSUES[error_type]
    if error_type.startswith(_RANGE_PREFIXES):
        return "Out of range"
    for suffix, text in _ISSUE_SUFFIXES:
        if error_type.endswith(suffix):
            return text
    return GENERIC_ISSUE


def safe_details(errors: Sequence[Mapping[str, Any]], body_type: Any | None) -> tuple[ErrorDetail, ...]:
    schema = _schema(body_type)
    return tuple(
        ErrorDetail(
            field=_field(tuple(err.get("loc", ())), str(err.get("type", "")), schema),
            issue=issue_for(str(err.get("type", ""))),
        )
        for err in errors
    )


def _schema(body_type: Any | None) -> Mapping[str, Any]:
    if body_type is None:
        return {}
    return TypeAdapter(body_type).json_schema(by_alias=True)


def _field(loc: tuple[Any, ...], error_type: str, schema: Mapping[str, Any]) -> str | None:
    if not loc:
        return None
    where, rest = str(loc[0]), loc[1:]
    if where in _PARAM_LOCATIONS:
        # Parameter names are declared by the route; only the name itself is kept.
        return f"{where}.{rest[0]}" if rest and isinstance(rest[0], str) else where
    if where != "body" or error_type == "json_invalid":
        return where
    parts = _resolve(rest, schema, schema.get("$defs", {}), error_type)
    return ".".join(["body", *parts])


def _resolve(loc: tuple[Any, ...], schema: Mapping[str, Any], defs: Mapping[str, Any], error_type: str) -> list[str]:
    parts: list[str] = []
    node: Mapping[str, Any] | None = schema
    for index, segment in enumerate(loc):
        last = index == len(loc) - 1
        node = _deref(node, defs)
        props = node.get("properties", {}) if node is not None else {}
        if isinstance(segment, str) and segment in props:
            parts.append(segment)
            node = props[segment]
        elif isinstance(segment, int) and node is not None and isinstance(node.get("items"), Mapping):
            parts.append(str(segment))
            node = node["items"]
        elif last and error_type == "extra_forbidden":
            parts.append(EXTRA)
        elif node is not None and isinstance(node.get("additionalProperties"), Mapping):
            parts.append(WILDCARD)
            node = node["additionalProperties"]
        else:
            parts.append(WILDCARD)
            node = None
    return parts


def _deref(node: Mapping[str, Any] | None, defs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Follow `$ref`s, and step into the first non-null branch of an `anyOf` (an optional field)."""
    seen: set[str] = set()
    while node is not None:
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in seen or name not in defs:
                return None
            seen.add(name)
            node = defs[name]
            continue
        branches = [b for b in node.get("anyOf", ()) if b.get("type") != "null"]
        if branches and "properties" not in node:
            node = branches[0]
            continue
        return node
    return None
