"""Conversations, messages and proposals in MongoDB, through tenant repositories only (PRD §7.6, §7.9).

`conversations` and `messages` are the Run Assistant's memory (PRD §7.6); `chat_proposals` is the API's
alone, outside the agent's read grant: only the service writes a proposal, so the agent cannot forge a
confirmable one. One conversation per (org, run, user). Message content is stored already redacted.
A proposal moves `pending → confirmed | failed` by compare-and-set, so one proposal runs at most once.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from onb_data import DataStore

__all__ = ["CONVERSATIONS", "MESSAGES", "PROPOSALS", "ChatStore"]

CONVERSATIONS: Final = "conversations"
MESSAGES: Final = "messages"
PROPOSALS: Final = "chat_proposals"
MAX_HISTORY: Final = 200


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


class ChatStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._conversations = store.tenant(CONVERSATIONS)
        self._messages = store.tenant(MESSAGES)
        self._proposals = store.tenant(PROPOSALS)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(CONVERSATIONS, [("org_id", 1), ("run_id", 1), ("user_id", 1)], unique=True)
        await self._store.ensure_index(MESSAGES, [("org_id", 1), ("conversation_id", 1), ("created_at", 1)])
        await self._store.ensure_index(PROPOSALS, [("org_id", 1), ("run_id", 1), ("status", 1)])

    async def conversation(self, run_id: str, user_id: str, *, at: datetime) -> dict[str, Any]:
        """This user's conversation about the run, created on first use."""
        found = await self._conversations.find_one({"run_id": run_id, "user_id": user_id})
        if found is not None:
            return found
        document = {"_id": new_id("conv"), "run_id": run_id, "user_id": user_id, "created_at": at}
        await self._conversations.update_one(
            {"run_id": run_id, "user_id": user_id}, {"$setOnInsert": document}, upsert=True
        )
        stored = await self._conversations.find_one({"run_id": run_id, "user_id": user_id})
        assert stored is not None  # noqa: S101 - just upserted in this tenant
        return stored

    async def find_conversation(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        return await self._conversations.find_one({"run_id": run_id, "user_id": user_id})

    async def add_message(
        self,
        conversation: Mapping[str, Any],
        *,
        role: str,
        content: str,
        at: datetime,
        status: str | None = None,
        citations: Sequence[tuple[str, str]] = (),
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "_id": new_id("msg"),
            "conversation_id": conversation["_id"],
            "run_id": conversation["run_id"],
            "user_id": conversation["user_id"],
            "role": role,
            "content": content,
            "status": status,
            "citations": [{"run_id": run, "task_id": task} for run, task in citations],
            "proposal_id": proposal_id,
            "created_at": at,
        }
        await self._messages.insert_one(document)
        return document

    async def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return await self._messages.find(
            {"conversation_id": conversation_id}, sort=[("created_at", 1), ("_id", 1)], limit=MAX_HISTORY
        )

    # --- proposals -----------------------------------------------------------------------------

    async def add_proposal(
        self,
        conversation: Mapping[str, Any],
        *,
        type: str,
        task_id: str,
        at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "_id": new_id("prop"),
            "conversation_id": conversation["_id"],
            "run_id": conversation["run_id"],
            "user_id": conversation["user_id"],
            "type": type,
            "task_id": task_id,
            "status": "pending",
            "created_at": at,
            "expires_at": expires_at,
        }
        await self._proposals.insert_one(document)
        return document

    async def proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return await self._proposals.find_one({"_id": proposal_id})

    async def proposals(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        found = await self._proposals.find({"_id": {"$in": list(ids)}})
        return {p["_id"]: p for p in found}

    async def claim(self, proposal_id: str, *, at: datetime) -> bool:
        """pending → confirmed, once. False when it was not pending (already used) or has expired."""
        outcome = await self._proposals.update_one(
            {"_id": proposal_id, "status": "pending", "expires_at": {"$gt": at}},
            {"$set": {"status": "confirmed", "confirmed_at": at}},
        )
        return outcome.matched == 1

    async def mark_failed(self, proposal_id: str) -> None:
        await self._proposals.update_one({"_id": proposal_id, "status": "confirmed"}, {"$set": {"status": "failed"}})
