"""One send per grant: the claim a telegram send takes before anything is resolved (NOTIF-1).

A `notify_sends` document (tenant) per verified grant: `{_id, org_id, grant_digest, args_digest, agent,
run_id, status: pending | sent | failed, outbox_id, at}`, unique on `{org_id, grant_digest}`. The first
call with a grant inserts it and owns the send; every other call with that grant finds it:

* same arguments, same agent and run: the first send's answer (`sent` → a replay), after waiting for a
  `pending` one to finish; a `failed` or still-pending one is refused without a second failure report;
* anything else is a used grant (`conflict`), refused `replayed` without touching the vault.

So a retry of a call that is still in flight, or that failed after the value was resolved, never turns
into a second resolution or a spurious task failure.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from onb_data import DataStore
from pymongo.errors import DuplicateKeyError

from .audit import Caller

__all__ = ["SENDS_COLLECTION", "Claim", "SendClaims", "ensure_send_indexes"]

SENDS_COLLECTION: Final = "notify_sends"
ClaimStatus = Literal["owned", "sent", "pending", "failed", "conflict"]


@dataclass(frozen=True, slots=True)
class Claim:
    status: ClaimStatus
    outbox_id: str | None = None


async def ensure_send_indexes(store: DataStore) -> None:
    await store.ensure_index(SENDS_COLLECTION, [("org_id", 1), ("grant_digest", 1)], unique=True, name="org_grant")


class SendClaims:
    """Call inside the caller's tenant scope."""

    def __init__(self, store: DataStore, *, wait_s: float = 5.0, poll_s: float = 0.05) -> None:
        self._store = store
        self._wait_s = wait_s
        self._poll_s = poll_s

    def _repo(self) -> Any:
        return self._store.tenant(SENDS_COLLECTION)

    async def claim(self, caller: Caller, grant_digest: str, args_digest: str) -> Claim:
        document = {
            "_id": secrets.token_hex(12),
            "grant_digest": grant_digest,
            "args_digest": args_digest,
            "agent": caller.agent,
            "run_id": caller.run_id,
            "status": "pending",
            "outbox_id": None,
            "at": datetime.now(UTC),
        }
        try:
            await self._repo().insert_one(document)
            return Claim("owned")
        except DuplicateKeyError:
            return await self._existing(caller, grant_digest, args_digest)

    async def _existing(self, caller: Caller, grant_digest: str, args_digest: str) -> Claim:
        deadline = asyncio.get_running_loop().time() + self._wait_s
        while True:
            found = await self._repo().find_one({"grant_digest": grant_digest})
            if found is None:  # released after a rejection: the grant is spent or refused either way
                return Claim("conflict")
            same = (found.get("args_digest"), found.get("agent"), found.get("run_id")) == (
                args_digest,
                caller.agent,
                caller.run_id,
            )
            if not same:
                return Claim("conflict")
            if found.get("status") != "pending" or asyncio.get_running_loop().time() >= deadline:
                return Claim(found.get("status", "pending"), found.get("outbox_id"))
            await asyncio.sleep(self._poll_s)

    async def mark_sent(self, grant_digest: str, outbox_id: str) -> None:
        sent = {"status": "sent", "outbox_id": outbox_id}
        await self._repo().update_one({"grant_digest": grant_digest}, {"$set": sent})

    async def mark_failed(self, grant_digest: str) -> None:
        await self._repo().update_one({"grant_digest": grant_digest}, {"$set": {"status": "failed"}})

    async def release(self, grant_digest: str) -> None:
        """A refused grant: nothing was resolved, so the claim goes (the refusal stands on its own)."""
        await self._repo().delete_one({"grant_digest": grant_digest, "status": "pending"})
