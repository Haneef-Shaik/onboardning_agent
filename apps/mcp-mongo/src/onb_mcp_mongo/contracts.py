"""The JSON-schema contract of every mcp-mongo tool (PRD §7.5 tool table).

Argument models are closed (`additionalProperties: false`), so an agent cannot slip in `org_id` or
any other unknown argument. The schemas served by `tools/list` are generated from these models,
and calls are validated against the same models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WithJsonSchema

from .call_context import COLLECTION_PATTERN

__all__ = ["CONTRACTS", "MAX_FIND_LIMIT", "MAX_PIPELINE_STAGES", "MAX_RESULT_DOCUMENTS", "ToolContract"]

MAX_FIND_LIMIT = 100
# aggregate has no caller limit, so its results are capped at the same size as find's.
MAX_RESULT_DOCUMENTS = MAX_FIND_LIMIT
MAX_PIPELINE_STAGES = 20
MAX_SKIP = 10_000

Collection = Annotated[str, Field(pattern=COLLECTION_PATTERN, description="Collection name")]
Query = Annotated[dict[str, Any], Field(description="MongoDB filter; never names org_id")]
Field_ = Annotated[str, Field(min_length=1, max_length=256)]


def _direction(value: int) -> int:
    if value not in (1, -1):
        raise ValueError("sort direction must be 1 or -1")
    return value


# A strict int (so `true` or `1.0` is not taken for 1), served as `enum: [1, -1]`.
Direction = Annotated[
    int, Field(strict=True), AfterValidator(_direction), WithJsonSchema({"type": "integer", "enum": [1, -1]})
]


class _Args(ArgModelBase):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)


class ListCollectionsArgs(_Args):
    pass


class FindArgs(_Args):
    collection: Collection
    filter: Query = Field(default_factory=dict)
    projection: dict[str, Any] | None = None
    sort: list[tuple[Field_, Direction]] | None = None
    skip: Annotated[int, Field(strict=True, ge=0, le=MAX_SKIP)] = 0
    limit: Annotated[int, Field(strict=True, ge=1, le=MAX_FIND_LIMIT, description="Required, 1-100")]


class CountArgs(_Args):
    collection: Collection
    filter: Query = Field(default_factory=dict)


class AggregateArgs(_Args):
    collection: Collection
    pipeline: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=MAX_PIPELINE_STAGES)]


class InsertOneArgs(_Args):
    collection: Collection
    document: Annotated[dict[str, Any], Field(description="The document; org_id is set by the server")]


class UpdateOneArgs(_Args):
    collection: Collection
    filter: Annotated[dict[str, Any], Field(min_length=1, description="Non-empty filter; never names org_id")]
    update: Annotated[dict[str, Any], Field(min_length=1, description="Update operators such as $set")]


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionsResult(_Result):
    collections: list[str]


class DocumentsResult(_Result):
    documents: list[dict[str, Any]]
    count: int


class CountResult(_Result):
    count: int


class InsertResult(_Result):
    inserted_id: str
    count: int


class UpdateResult(_Result):
    matched: int
    modified: int
    count: int


@dataclass(frozen=True)
class ToolContract:
    args: type[_Args]
    result: type[_Result]
    description: str
    read_only: bool


CONTRACTS: dict[str, ToolContract] = {
    "list_collections": ToolContract(
        ListCollectionsArgs, CollectionsResult, "The collections this agent may use.", read_only=True
    ),
    "find": ToolContract(
        FindArgs, DocumentsResult, "Find documents in the caller's org. `limit` (1-100) is required.", read_only=True
    ),
    "count": ToolContract(CountArgs, CountResult, "Count documents in the caller's org.", read_only=True),
    "aggregate": ToolContract(
        AggregateArgs,
        DocumentsResult,
        "Aggregate within the caller's org. Stages: $match $project $group $sort $limit $count $unwind "
        "$addFields. At most 100 documents are returned.",
        read_only=True,
    ),
    "insert_one": ToolContract(
        InsertOneArgs, InsertResult, "Insert one document; the server sets org_id.", read_only=False
    ),
    "update_one": ToolContract(
        UpdateOneArgs, UpdateResult, "Update one document in the caller's org with update operators.", read_only=False
    ),
}
