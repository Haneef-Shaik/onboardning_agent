"""The in-app channel: a `notifications` record a platform user sees in the web app (PRD FR-N1).

    {_id, org_id, user_id, kind, body, ref: {type, id} | null, run_id, agent, dedupe_key?,
     created_at, read_at: null}

Two senders:
* an agent, through `notify_send(channel="in_app", recipient_user_id=…)`; the recipient must be a
  user of the caller's org (else `UnknownRecipientError`, the same answer as a user that does not
  exist);
* the platform, through the `notify.in_app` event consumer: a decided change request tells its
  requester (TB-2's `change_request.decided`); a blocked run tells the org's HR and HR_ADMIN users
  (RUN-2's `run.blocked`). The handler is idempotent: each (event, user) pair is stored once, so a
  redelivered job writes nothing new (EVT-1 delivers at least once).

Platform bodies are fixed text built from codes and ids, never from payload strings.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts.notify import CHANGE_REQUEST_DECIDED, NOTIFICATIONS_COLLECTION, RUN_BLOCKED
from onb_data import DataStore
from onb_eventbus import Handler, JobContext
from pymongo.errors import DuplicateKeyError

__all__ = [
    "USERS_COLLECTION",
    "InAppChannel",
    "MalformedEventError",
    "UnknownRecipientError",
    "ensure_in_app_indexes",
    "in_app_handler",
]

USERS_COLLECTION: Final = "users"
NOTIFY_ROLES: Final = ("HR", "HR_ADMIN")  # who hears about a blocked run
_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_DECISIONS: Final = {"accepted": "accepted", "rejected": "rejected"}


class UnknownRecipientError(LookupError):
    """No such user in the caller's org."""


class MalformedEventError(ValueError):
    """The event payload breaks its contract; the job is retried and then dead-lettered."""


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_in_app_indexes(store: DataStore) -> None:
    await store.ensure_index(NOTIFICATIONS_COLLECTION, [("org_id", 1), ("user_id", 1), ("created_at", -1)])
    await store.ensure_index(
        NOTIFICATIONS_COLLECTION,
        [("org_id", 1), ("dedupe_key", 1)],
        unique=True,
        partial={"dedupe_key": {"$exists": True}},
        name="org_dedupe_key",
    )


class InAppChannel:
    """The `in_app` adapter. Call inside the tenant scope of the org being notified."""

    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def deliver(
        self,
        user_id: str,
        *,
        kind: str,
        body: str,
        ref: Mapping[str, str] | None = None,
        run_id: str | None = None,
        agent: str | None = None,
        dedupe_key: str | None = None,
    ) -> tuple[str, bool]:
        """Store the notification; `(id, replayed)`. A known `dedupe_key` stores nothing new."""
        if not await self._store.tenant(USERS_COLLECTION).find_one({"_id": user_id}, projection={"_id": 1}):
            raise UnknownRecipientError("no such recipient in this org")
        document: dict[str, Any] = {
            "_id": secrets.token_hex(12),
            "user_id": user_id,
            "kind": kind,
            "body": body,
            "ref": dict(ref) if ref is not None else None,
            "run_id": run_id,
            "agent": agent,
            "created_at": _now(),
            "read_at": None,
        }
        if dedupe_key is not None:
            document["dedupe_key"] = dedupe_key
        try:
            await self._store.tenant(NOTIFICATIONS_COLLECTION).insert_one(document)
        except DuplicateKeyError:
            existing = await self._store.tenant(NOTIFICATIONS_COLLECTION).find_one({"dedupe_key": dedupe_key})
            if existing is None:  # the duplicate was removed in between: let the bus retry the job
                raise
            return str(existing["_id"]), True
        return str(document["_id"]), False


def _id(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MalformedEventError(f"{name} must be an id")
    return value


async def _change_request_decided(channel: InAppChannel, job: JobContext) -> None:
    request_id = _id(job.payload, "change_request_id")
    requester = _id(job.payload, "requested_by")
    decision = _DECISIONS.get(str(job.payload.get("decision")))
    if decision is None:
        raise MalformedEventError("decision must be accepted or rejected")
    try:
        await channel.deliver(
            requester,
            kind=CHANGE_REQUEST_DECIDED,
            body=f"Your template change request was {decision}.",
            ref={"type": "change_request", "id": request_id},
            dedupe_key=f"{job.event_id}:{requester}",
        )
    except UnknownRecipientError:
        return  # the requester has left the org: nobody to tell, and a retry would not change that


async def _run_blocked(store: DataStore, channel: InAppChannel, job: JobContext) -> None:
    run_id = _id(job.payload, "run_id")
    task_id = _id(job.payload, "task_id")
    recipients = await store.tenant(USERS_COLLECTION).find({"role": {"$in": list(NOTIFY_ROLES)}}, limit=200)
    for user in recipients:
        user_id = str(user["_id"])
        await channel.deliver(
            user_id,
            kind=RUN_BLOCKED,
            body=f"Run {run_id} is blocked: task {task_id} failed after its last attempt.",
            ref={"type": "run", "id": run_id},
            run_id=run_id,
            dedupe_key=f"{job.event_id}:{user_id}",
        )


def in_app_handler(store: DataStore) -> Handler:
    """The `notify.in_app` consumer. The bus has already set the job's tenant scope."""
    channel = InAppChannel(store)

    async def handle(job: JobContext) -> None:
        if job.event_type == CHANGE_REQUEST_DECIDED:
            await _change_request_decided(channel, job)
        elif job.event_type == RUN_BLOCKED:
            await _run_blocked(store, channel, job)
        else:
            raise MalformedEventError(f"notify.in_app is not subscribed to {job.event_type}")

    return handle
