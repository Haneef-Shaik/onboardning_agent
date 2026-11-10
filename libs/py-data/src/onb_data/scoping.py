"""Pure rules that put a query, document, update or pipeline inside one org (PRD §7.9 Data access).

Every function returns a new object and never changes its input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import InvalidIndexError, TenantViolationError

__all__ = [
    "CROSS_COLLECTION_STAGES",
    "ORG_FIELD",
    "check_pipeline",
    "check_update",
    "plain_filter",
    "scoped_document",
    "scoped_filter",
    "scoped_pipeline",
    "scoped_update",
    "validate_index_keys",
]

Document = dict[str, Any]
IndexKey = tuple[str, int | str]

ORG_FIELD = "org_id"

# Stages that read or write another collection would step outside the `$match` on `org_id`.
CROSS_COLLECTION_STAGES = frozenset({"$lookup", "$graphLookup", "$unionWith", "$out", "$merge"})

_INDEX_DIRECTIONS: frozenset[int | str] = frozenset({1, -1, "text", "hashed", "2dsphere"})


def _touches_org(path: object) -> bool:
    return isinstance(path, str) and (path == ORG_FIELD or path.startswith(f"{ORG_FIELD}."))


def plain_filter(query: Mapping[str, Any] | None) -> Document:
    if query is None:
        return {}
    if not isinstance(query, Mapping):
        raise TypeError("a filter must be a mapping")
    return dict(query)


def scoped_filter(query: Mapping[str, Any] | None, org_id: str) -> Document:
    """The filter ANDed with `org_id`. A caller may name its own org, never another one."""
    base = plain_filter(query)
    if ORG_FIELD in base and base[ORG_FIELD] != org_id:
        raise TenantViolationError("a filter may not choose org_id: it comes from the tenant context")
    return {**base, ORG_FIELD: org_id}


def scoped_document(document: Mapping[str, Any], org_id: str) -> Document:
    if not isinstance(document, Mapping):
        raise TypeError("a document must be a mapping")
    if ORG_FIELD in document and document[ORG_FIELD] != org_id:
        raise TenantViolationError("a document may not carry another org_id")
    return {**document, ORG_FIELD: org_id}


def check_update(update: Mapping[str, Any], *, tenant: bool) -> Document:
    """An operator update (`{"$set": {...}}`), copied. Tenant updates may not touch `org_id`."""
    if not isinstance(update, Mapping):
        raise TypeError("an update must be a mapping of update operators")
    if not update or not all(isinstance(op, str) and op.startswith("$") for op in update):
        raise ValueError("an update must use update operators such as $set (no replacement documents)")
    checked: Document = {}
    for op, fields in update.items():
        if not isinstance(fields, Mapping):
            raise TypeError(f"update operator {op} needs a mapping of fields")
        renamed_to = fields.values() if op == "$rename" else ()
        if tenant and any(_touches_org(path) for path in [*fields, *renamed_to]):
            raise TenantViolationError("an update may not change org_id")
        checked[op] = dict(fields)
    return checked


def scoped_update(update: Mapping[str, Any], org_id: str, *, upsert: bool) -> Document:
    """The update checked; on upsert, `$setOnInsert` also sets `org_id` (trap: upserts must set it)."""
    checked = check_update(update, tenant=True)
    if not upsert:
        return checked
    on_insert = {**checked.get("$setOnInsert", {}), ORG_FIELD: org_id}
    return {**checked, "$setOnInsert": on_insert}


def _find_cross_collection_stage(node: object) -> str | None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in CROSS_COLLECTION_STAGES:
                return str(key)
            found = _find_cross_collection_stage(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_cross_collection_stage(item)
            if found:
                return found
    return None


def check_pipeline(pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
    """A copy of the pipeline. Each stage is one `$operator`; none may reach another collection."""
    if not isinstance(pipeline, list | tuple):
        raise TypeError("a pipeline must be a list of stages")
    stages: list[Document] = []
    for stage in pipeline:
        if not isinstance(stage, Mapping) or len(stage) != 1 or not str(next(iter(stage))).startswith("$"):
            raise TypeError("each pipeline stage must be a mapping with exactly one $operator")
        stages.append(dict(stage))
    forbidden = _find_cross_collection_stage(stages)
    if forbidden:
        raise TenantViolationError(f"pipeline stage {forbidden} reads or writes another collection")
    return stages


def scoped_pipeline(pipeline: Sequence[Mapping[str, Any]], org_id: str) -> list[Document]:
    """`$match` on `org_id` first, whatever the caller's first stage is (trap: a leading `$sort`)."""
    return [{"$match": {ORG_FIELD: org_id}}, *check_pipeline(pipeline)]


def _index_key(item: object) -> IndexKey:
    if not isinstance(item, list | tuple) or len(item) != 2:
        raise InvalidIndexError("each index key must be a (field, direction) pair")
    field, direction = item
    if not isinstance(field, str) or not field:
        raise InvalidIndexError("an index field must be a non-empty string")
    if isinstance(direction, bool) or direction not in _INDEX_DIRECTIONS:
        raise InvalidIndexError(f"index direction for {field} must be one of 1, -1, 'text', 'hashed', '2dsphere'")
    return field, direction


def validate_index_keys(keys: Sequence[IndexKey], *, tenant: bool) -> list[IndexKey]:
    """Index keys, checked. A tenant index starts with `org_id`, so unique ones are per org."""
    if not isinstance(keys, list | tuple) or not keys:
        raise InvalidIndexError("index keys must be a non-empty list of (field, direction) pairs")
    spec = [_index_key(item) for item in keys]
    fields = [field for field, _ in spec]
    if len(set(fields)) != len(fields):
        raise InvalidIndexError("an index may not name a field twice")
    if tenant and fields[0] != ORG_FIELD:
        raise InvalidIndexError("an index on a tenant collection must start with org_id")
    return spec
