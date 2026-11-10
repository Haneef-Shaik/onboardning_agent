"""The six tool handlers (PRD §7.5). Each runs inside `tenant_scope(ctx.org_id)` and reaches
MongoDB only through a `TenantRepository`, which adds the org to every filter, document and
pipeline. The policy checks run first, so a rejected call never reaches the driver.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from bson import Decimal128, ObjectId
from onb_data import DataStore, tenant_scope

from . import policy
from .call_context import CallContext
from .contracts import (
    MAX_RESULT_DOCUMENTS,
    CollectionsResult,
    CountResult,
    DocumentsResult,
    InsertResult,
    UpdateResult,
)

__all__ = ["aggregate", "count", "find", "insert_one", "list_collections", "to_json", "update_one"]

Document = dict[str, Any]


def to_json(value: Any) -> Any:
    """BSON values as plain JSON: ids and decimals as strings, datetimes as ISO 8601."""
    if isinstance(value, Mapping):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, ObjectId | Decimal128 | Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$binary_length": len(value)}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _documents(docs: list[Document]) -> DocumentsResult:
    return DocumentsResult(documents=[to_json(doc) for doc in docs], count=len(docs))


async def list_collections(store: DataStore, ctx: CallContext) -> CollectionsResult:
    return CollectionsResult(collections=sorted(ctx.readable))


async def find(
    store: DataStore,
    ctx: CallContext,
    *,
    collection: str,
    filter: Document,  # the MCP argument name
    projection: Document | None,
    sort: list[tuple[str, int]] | None,
    skip: int,
    limit: int,
) -> DocumentsResult:
    policy.check_readable(ctx, collection)
    policy.reject_org_id(filter, "filter")
    with tenant_scope(ctx.org_id):
        docs = await store.tenant(collection).find(
            policy.decode_ids(filter),
            projection=projection,
            sort=[(field, direction) for field, direction in sort] if sort else None,
            skip=skip,
            limit=limit,
        )
    return _documents(docs)


async def count(store: DataStore, ctx: CallContext, *, collection: str, filter: Document) -> CountResult:
    policy.check_readable(ctx, collection)
    policy.reject_org_id(filter, "filter")
    with tenant_scope(ctx.org_id):
        total = await store.tenant(collection).count(policy.decode_ids(filter))
    return CountResult(count=total)


async def aggregate(
    store: DataStore, ctx: CallContext, *, collection: str, pipeline: list[Document]
) -> DocumentsResult:
    policy.check_readable(ctx, collection)
    policy.check_pipeline(pipeline)
    policy.reject_org_id(pipeline, "pipeline")
    capped = [*policy.decode_ids(pipeline), {"$limit": MAX_RESULT_DOCUMENTS}]
    with tenant_scope(ctx.org_id):
        docs = await store.tenant(collection).aggregate(capped)
    return _documents(docs)


async def insert_one(store: DataStore, ctx: CallContext, *, collection: str, document: Document) -> InsertResult:
    policy.check_writable(ctx, collection)
    policy.reject_org_id(document, "document")
    with tenant_scope(ctx.org_id):
        inserted = await store.tenant(collection).insert_one(policy.decode_ids(document))
    return InsertResult(inserted_id=str(inserted), count=1)


async def update_one(
    store: DataStore,
    ctx: CallContext,
    *,
    collection: str,
    filter: Document,
    update: Document,
) -> UpdateResult:
    policy.check_writable(ctx, collection)
    policy.reject_org_id(filter, "filter")
    policy.reject_org_id(update, "update")
    policy.check_update_operators(update)
    with tenant_scope(ctx.org_id):
        outcome = await store.tenant(collection).update_one(policy.decode_ids(filter), policy.decode_ids(update))
    return UpdateResult(matched=outcome.matched, modified=outcome.modified, count=outcome.matched)
