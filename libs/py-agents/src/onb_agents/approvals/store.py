"""Approval records in MongoDB, through tenant repositories only (PRD §7.9 Data access).

A record (`approvals`, tenant collection; the shape the eval fixtures already use):

    {_id: "appr_<hash>", org_id, run_id, task_id, hire_id, kind, rule, status, approver_id,
     approver_role, title, request: {items}, packet, packet_failure, requested_at, sla_hours,
     decision: {decision, comment, channel, decided_by, decided_at} | None}

The id is a hash of (run, task, kind, rule), so a redelivered job opens the same approval instead of a
second one. `packet` is the Approvals Agent's masked output (`ApprovalPacket`), `packet_failure` a code
when the agent could not prepare one: the human is still asked. Holds ids, codes and masked text only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from onb_contracts.approvals import APPROVALS_COLLECTION, ApprovalKind, ApprovalStatus, approval_id_for
from onb_data import DataStore, current_tenant
from pymongo.errors import DuplicateKeyError

__all__ = ["ApprovalSpec", "ApprovalStore"]

_ID_HEX: Final = 24
MAX_ITEMS: Final = 50


@dataclass(frozen=True, slots=True)
class ApprovalSpec:
    run_id: str
    task_id: str
    hire_id: str
    kind: ApprovalKind
    rule: str | None
    approver_id: str | None
    approver_role: str | None
    title: str
    items: Sequence[str]
    sla_hours: int | None

    def __post_init__(self) -> None:
        if (self.kind == "tool_call") != bool(self.rule):
            raise ValueError("only a tool_call approval has a rule, and it needs one")
        if (self.approver_id is None) == (self.approver_role is None):
            raise ValueError("an approval has an approver id or an approver role, not both and not neither")

    @property
    def approval_id(self) -> str:
        key = "\x1f".join((self.run_id, self.task_id, self.kind, self.rule or ""))
        return approval_id_for(hashlib.sha256(key.encode()).hexdigest()[:_ID_HEX])


class ApprovalStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._approvals = store.tenant(APPROVALS_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(APPROVALS_COLLECTION, [("org_id", 1), ("run_id", 1), ("task_id", 1)])
        await self._store.ensure_index(
            APPROVALS_COLLECTION, [("org_id", 1), ("approver_id", 1), ("status", 1), ("requested_at", -1)]
        )

    async def create(self, spec: ApprovalSpec, *, now: datetime) -> dict[str, Any]:
        """Open the approval, or return the one this (run, task, kind, rule) already opened, unchanged."""
        document: dict[str, Any] = {
            "_id": spec.approval_id,
            "run_id": spec.run_id,
            "task_id": spec.task_id,
            "hire_id": spec.hire_id,
            "kind": spec.kind,
            "rule": spec.rule,
            "status": "pending",
            "approver_id": spec.approver_id,
            "approver_role": spec.approver_role,
            "title": spec.title,
            "request": {"items": list(spec.items)[:MAX_ITEMS]},
            "packet": None,
            "packet_failure": None,
            "requested_at": now,
            "sla_hours": spec.sla_hours,
            "decision": None,
        }
        try:
            await self._approvals.insert_one(document)
        except DuplicateKeyError:
            existing = await self.get(spec.approval_id)
            if existing is None:  # removed in between: let the caller retry
                raise
            return existing
        return {**document, "org_id": current_tenant().org_id}

    async def get(self, approval_id: str) -> dict[str, Any] | None:
        return await self._approvals.find_one({"_id": approval_id})

    async def for_task(self, run_id: str, task_id: str) -> list[dict[str, Any]]:
        return await self._approvals.find({"run_id": run_id, "task_id": task_id}, sort=[("requested_at", 1)])

    async def set_packet(
        self, approval_id: str, packet: Mapping[str, Any] | None, *, failure: str | None = None
    ) -> None:
        """Keep the Approvals Agent's packet (or why there is none), once: the first one stays."""
        changes = {"packet": dict(packet) if packet is not None else None, "packet_failure": failure}
        await self._approvals.update_one(
            {"_id": approval_id, "packet": None, "packet_failure": None}, {"$set": changes}
        )

    async def approved_rules(self, run_id: str, task_id: str) -> tuple[str, ...]:
        """The `requires_approval` rules a human approved for this task: the only source of an agent's
        `approved:<rule>` scopes (ADR 0010)."""
        found = await self._approvals.find(
            {"run_id": run_id, "task_id": task_id, "kind": "tool_call", "status": "approved"},
            sort=[("requested_at", 1)],
        )
        return tuple(str(doc["rule"]) for doc in found)

    async def cancel_pending(self, run_id: str, *, at: datetime) -> list[str]:
        """Cancel the run's undecided approvals (the run ended); returns their ids."""
        pending = await self._approvals.find({"run_id": run_id, "status": "pending"})
        cancelled: list[str] = []
        for doc in pending:
            outcome = await self._approvals.update_one(
                {"_id": doc["_id"], "status": "pending"}, {"$set": {"status": "cancelled", "cancelled_at": at}}
            )
            if outcome.matched:
                cancelled.append(str(doc["_id"]))
        return cancelled

    @staticmethod
    def _visible(user_id: str, role: str | None, status: ApprovalStatus | None) -> dict[str, Any]:
        branches: list[dict[str, Any]] = [{"approver_id": user_id}]
        if role is not None:
            branches.append({"approver_id": None, "approver_role": role})
        query: dict[str, Any] = {"$or": branches}
        if status is not None:
            query["status"] = status
        return query

    async def list_for(
        self, *, user_id: str, role: str | None, status: ApprovalStatus | None, skip: int, limit: int
    ) -> list[dict[str, Any]]:
        """The approvals this user may decide (theirs, or their role's), newest first."""
        return await self._approvals.find(
            self._visible(user_id, role, status), sort=[("requested_at", -1), ("_id", -1)], skip=skip, limit=limit
        )

    async def list_all(self, *, status: ApprovalStatus | None, skip: int, limit: int) -> list[dict[str, Any]]:
        """The org's approvals (HR's view), newest first."""
        query: dict[str, Any] = {"status": status} if status is not None else {}
        return await self._approvals.find(query, sort=[("requested_at", -1), ("_id", -1)], skip=skip, limit=limit)

    async def count_all(self, *, status: ApprovalStatus | None) -> int:
        return await self._approvals.count({"status": status} if status is not None else {})

    async def count_for(self, *, user_id: str, role: str | None, status: ApprovalStatus | None) -> int:
        return await self._approvals.count(self._visible(user_id, role, status))

    async def decide_if_pending(self, approval_id: str, decision: Mapping[str, Any], status: ApprovalStatus) -> bool:
        """The one decision write: applies only while the approval is `pending`. False when another
        decision (or a cancel) got there first; nothing is written then."""
        outcome = await self._approvals.update_one(
            {"_id": approval_id, "status": "pending"}, {"$set": {"status": status, "decision": dict(decision)}}
        )
        return outcome.matched == 1
