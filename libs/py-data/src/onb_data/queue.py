"""The job queue seen by workers (EVT-1; PRD §7.2, §7.9 Event bus).

A worker claims the next job before it knows the job's org, so the claim cannot be org-scoped.
`QueueRepository` is the one narrow, cross-org path for that, over `jobs` and `dead_letters` only:

- `claim`: one atomic `find_one_and_update` returning the claimed document;
- `update_leased` / `delete_leased`: writes that match `{_id, lease_token}`, so a worker whose lease
  has passed to another worker can no longer touch the job;
- `insert_one` / `find`: dead letters are written as given (a job without `org_id` stays without
  one) and read back for inspection, always with a bound.

Updates are operator updates that never touch `org_id`, so a worker cannot move a job to another org.

Producers never use it: they write jobs through `store.tenant("jobs")`, which sets `org_id` from the
context. The worker then sets the tenant context from the claimed job before running a consumer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pymongo import ReturnDocument

from . import scoping
from .errors import TenantViolationError
from .session import session_kwargs

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

__all__ = ["QUEUE_COLLECTIONS", "QueueRepository"]

Document = dict[str, Any]
SortSpec = Sequence[tuple[str, int]]

# Tenant collections that workers must also reach across orgs.
QUEUE_COLLECTIONS = frozenset({"jobs", "dead_letters"})
DEAD_LETTERS = "dead_letters"

# A claim may select on queue state only: never on `org_id`, an id or a job's content, so it can't be
# aimed at one org's jobs or used to search payloads.
CLAIM_FILTER_FIELDS = frozenset({"consumer", "status", "available_at", "locked_until", "attempts", "max_attempts"})
_CLAIM_COMBINATORS = frozenset({"$or", "$and"})
# A worker moves a job through its states; it never rewrites what the job does or whose it is.
WORKER_STATE_FIELDS = frozenset(
    {
        "status",
        "lease_token",
        "lease_owner",
        "locked_until",
        "available_at",
        "last_error",
        "claimed_at",
        "completed_at",
        "updated_at",
    }
)
_WORKER_OPERATORS = {"$set": WORKER_STATE_FIELDS, "$unset": WORKER_STATE_FIELDS, "$inc": frozenset({"attempts"})}


def _claim_filter(query: Mapping[str, Any]) -> Document:
    checked = scoping.plain_filter(query)

    def walk(node: Document) -> None:
        for key, value in node.items():
            if key in _CLAIM_COMBINATORS and isinstance(value, list):
                for branch in value:
                    if not isinstance(branch, dict):
                        raise TenantViolationError(f"a claim {key} needs a list of filters")
                    walk(branch)
            elif key not in CLAIM_FILTER_FIELDS:
                raise TenantViolationError(f"a claim may filter on queue state only, not {key!r}")

    walk(checked)
    return checked


def _worker_update(update: Mapping[str, Any]) -> Document:
    checked = scoping.check_update(update, tenant=True)
    for op, fields in checked.items():
        allowed = _WORKER_OPERATORS.get(op)
        if allowed is None or not set(fields) <= allowed:
            raise TenantViolationError(f"a worker may only change queue state ({op} {sorted(fields)} refused)")
    return checked


def _leased(job_id: object, lease_token: object) -> Document:
    if isinstance(job_id, Mapping | list | tuple | set | frozenset):
        raise TypeError("a job id must be a single value, not an operator or a collection")
    if not isinstance(lease_token, str) or not lease_token:
        raise TypeError("a lease token must be a non-empty string")
    return {"_id": job_id, "lease_token": lease_token}


class QueueRepository:
    """Cross-org access to a queue collection, limited to what a worker needs."""

    __slots__ = ("_collection",)

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    @property
    def collection_name(self) -> str:
        return self._collection.name

    async def claim(
        self, query: Mapping[str, Any], update: Mapping[str, Any], *, sort: SortSpec | None = None
    ) -> Document | None:
        """Atomically update the first matching document and return it as updated, or `None`."""
        return await self._collection.find_one_and_update(
            _claim_filter(query),
            _worker_update(update),
            sort=sort,
            return_document=ReturnDocument.AFTER,
            **session_kwargs(),
        )

    async def update_leased(self, job_id: Any, lease_token: str, update: Mapping[str, Any]) -> bool:
        """Apply `update` if the caller still holds the lease. False when the lease is lost."""
        query = _leased(job_id, lease_token)
        result = await self._collection.update_one(
            scoping.plain_filter(query), _worker_update(update), **session_kwargs()
        )
        return result.matched_count == 1

    async def delete_leased(self, job_id: Any, lease_token: str) -> bool:
        """Delete the job if the caller still holds the lease. False when the lease is lost."""
        query = _leased(job_id, lease_token)
        result = await self._collection.delete_one(scoping.plain_filter(query), **session_kwargs())
        return result.deleted_count == 1

    def _dead_letters_only(self, operation: str) -> None:
        if self.collection_name != DEAD_LETTERS:
            raise TenantViolationError(
                f"{operation} is for dead_letters only: jobs are written by producers through tenant('jobs')"
            )

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        """Write a dead letter as given: a job that had no `org_id` keeps none."""
        self._dead_letters_only("insert_one")
        result = await self._collection.insert_one(scoping.plain_document(document), **session_kwargs())
        return result.inserted_id

    async def find(self, query: Mapping[str, Any], *, limit: int) -> list[Document]:
        """Read dead letters across orgs, for operators. Always bounded."""
        self._dead_letters_only("find")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("find on a queue collection needs a positive limit")
        cursor = self._collection.find(scoping.plain_filter(query), limit=limit, **session_kwargs())
        return await cursor.to_list()
