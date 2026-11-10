"""Rules checked before any database call (PRD §7.5).

MCP-1 owns: `org_id` never comes from the agent, collections come from the call context, aggregate
stages are allowlisted. The recursive operator validator (`$where`, `pii.*` and the rest) is MCP-2.
Every function leaves its input unchanged. A `ToolRejectedError` names the argument, never its value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from bson import ObjectId

from .call_context import CallContext

__all__ = [
    "ALLOWED_STAGES",
    "ORG_FIELD",
    "ToolRejectedError",
    "check_pipeline",
    "check_readable",
    "check_update_operators",
    "check_writable",
    "decode_ids",
    "reject_org_id",
]

ORG_FIELD = "org_id"
ALLOWED_STAGES = frozenset({"$match", "$project", "$group", "$sort", "$limit", "$count", "$unwind", "$addFields"})
_HEX24 = re.compile(r"[0-9a-fA-F]{24}")


class ToolRejectedError(Exception):
    """A call refused by policy. `reason` is a stable code for spans and audit."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _names_org(key: object) -> bool:
    return isinstance(key, str) and (key == ORG_FIELD or key.startswith(f"{ORG_FIELD}."))


def _mentions_org(node: object) -> bool:
    if isinstance(node, Mapping):
        return any(_names_org(k) or _names_org(v) or _mentions_org(v) for k, v in node.items())
    if isinstance(node, list | tuple):
        return any(_mentions_org(item) for item in node)
    return False


def reject_org_id(value: object, argument: str) -> None:
    """Refuse `org_id` as a key at any depth (and as a `$rename` target): tenant scope is the hub's."""
    if _mentions_org(value):
        raise ToolRejectedError(
            "org_id_not_allowed", f"{argument} may not name org_id: tenant scope comes from the hub"
        )


def check_readable(ctx: CallContext, collection: str) -> None:
    if collection not in ctx.readable:
        raise ToolRejectedError("collection_not_allowed", "collection is not readable by this agent")


def check_writable(ctx: CallContext, collection: str) -> None:
    if collection not in ctx.write:
        raise ToolRejectedError("collection_not_allowed", "collection is not writable by this agent")


def check_pipeline(pipeline: Sequence[Mapping[str, Any]]) -> None:
    for stage in pipeline:
        if not isinstance(stage, Mapping) or len(stage) != 1 or not str(next(iter(stage))).startswith("$"):
            raise ToolRejectedError("invalid_stage", "each pipeline stage must be one $stage operator")
        if next(iter(stage)) not in ALLOWED_STAGES:
            raise ToolRejectedError("stage_not_allowed", "pipeline stage is not on the allowlist")


def check_update_operators(update: Mapping[str, Any]) -> None:
    """An update is operators only (`$set`, `$inc`, ...): a replacement document could drop fields."""
    if not update or not all(isinstance(op, str) and op.startswith("$") for op in update):
        raise ToolRejectedError("invalid_update", "an update must use update operators such as $set")


def _as_object_id(node: Mapping[str, Any]) -> ObjectId | None:
    if len(node) == 1 and isinstance(node.get("$oid"), str) and _HEX24.fullmatch(node["$oid"]):
        return ObjectId(node["$oid"])
    return None


def decode_ids(node: Any) -> Any:
    """A copy with `{"$oid": "<24 hex>"}` turned into ObjectId. No other extended-JSON form is
    decoded, so agent input can never become BSON code, regexes or other special types."""
    if isinstance(node, Mapping):
        oid = _as_object_id(node)
        if oid is not None:
            return oid
        return {key: decode_ids(value) for key, value in node.items()}
    if isinstance(node, list | tuple):
        return [decode_ids(item) for item in node]
    return node
