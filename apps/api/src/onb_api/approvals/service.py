"""The approval service behind `GET /approvals` and `POST /approvals/{id}/decision` (PRD FR-H2, FR-H3, §10; HIL-1).

**Reading.** `mine` (the default) lists the approvals the caller may decide: theirs, or their role's (an
approval for "any HR user"). HR and HR_ADMIN may read the whole org's (`mine=false`); a MANAGER never sees
anyone else's. Every read is in the token's org, so another org's approval does not exist.

**Deciding.** One write, shared with the Telegram path (`ApprovalDecider`): the first decision wins, so a
second one, from the web or from Telegram, is 409. Someone who is not the approver gets 404, like an approval
that does not exist (the caller learns nothing about whose it is). The decision, its comment, its channel and
the user are stored and emitted as `approval.decided`, in one transaction with the audit record (FR-H3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from onb_agents.approvals import (
    AlreadyDecidedError,
    ApprovalDecider,
    ApprovalNotFoundError,
    ApprovalStore,
    NotApproverError,
)
from onb_contracts import ErrorCode
from onb_contracts.agent_outputs import ApprovalPacket
from onb_contracts.approvals import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalRecord,
    ApprovalStatus,
)
from onb_contracts.auth import Role
from onb_data import Auditor, DataStore
from onb_eventbus import EventBus
from pydantic import ValidationError

from onb_api.auth.tokens import Principal
from onb_api.errors import AppError

__all__ = ["ApprovalPage", "ApprovalService"]

_log = logging.getLogger("onb_api.approvals")
_ALREADY_DECIDED = "This approval has already been decided."


class ApprovalPage:
    __slots__ = ("records", "total")

    def __init__(self, records: tuple[ApprovalRecord, ...], total: int) -> None:
        self.records = records
        self.total = total


def _packet(doc: Mapping[str, Any]) -> ApprovalPacket | None:
    """The stored packet, or None for one that no longer meets the contract (it is never shown unchecked)."""
    stored = doc.get("packet")
    if stored is None:
        return None
    try:
        return ApprovalPacket.model_validate(stored)
    except ValidationError:
        _log.warning("approval %s: stored packet does not meet its contract", doc.get("_id"))
        return None


def _decision(doc: Mapping[str, Any]) -> ApprovalDecision | None:
    stored = doc.get("decision")
    return ApprovalDecision.model_validate(stored) if stored else None


def to_record(doc: Mapping[str, Any]) -> ApprovalRecord:
    """The record for a stored approval. Approvals written before a field existed (the eval fixtures') read
    with its default: a `task` approval titled by its task."""
    return ApprovalRecord(
        id=str(doc["_id"]),
        run_id=str(doc["run_id"]),
        task_id=str(doc["task_id"]),
        hire_id=str(doc.get("hire_id", "")),
        kind=doc.get("kind", "task"),
        rule=doc.get("rule"),
        status=doc["status"],
        approver_id=doc.get("approver_id"),
        approver_role=doc.get("approver_role"),
        title=str(doc.get("title") or doc["task_id"]),
        items=[str(i) for i in (doc.get("request") or {}).get("items", [])],
        packet=_packet(doc),
        requested_at=doc["requested_at"],
        sla_hours=doc.get("sla_hours"),
        decision=_decision(doc),
    )


def _readable(doc: Mapping[str, Any]) -> ApprovalRecord | None:
    """The record, or None (logged by id) for a document that is not an approval: one bad record must not
    take the whole inbox down."""
    try:
        return to_record(doc)
    except (KeyError, TypeError, ValueError):  # ValidationError is a ValueError
        _log.warning("approvals: skipped an unreadable document %s", doc.get("_id"))
        return None


class ApprovalService:
    def __init__(
        self,
        approvals: ApprovalStore,
        decider: ApprovalDecider,
        *,
        bus_indexes: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._approvals = approvals
        self._decider = decider
        self._bus_indexes = bus_indexes

    @classmethod
    def build(
        cls,
        store: DataStore,
        bus: EventBus,
        *,
        auditor: Auditor,
        bus_indexes: Callable[[], Awaitable[None]] | None = None,
    ) -> ApprovalService:
        """`bus` is `run_bus(store)` in production: the decision's event is `approval.decided`."""
        approvals = ApprovalStore(store)
        return cls(approvals, ApprovalDecider(approvals, bus, auditor=auditor), bus_indexes=bus_indexes)

    async def ensure_indexes(self) -> None:
        await self._approvals.ensure_indexes()
        if self._bus_indexes is not None:
            await self._bus_indexes()

    async def list(
        self, principal: Principal, *, mine: bool, status: ApprovalStatus | None, page: int, limit: int
    ) -> ApprovalPage:
        skip = (page - 1) * limit
        if mine:
            kwargs: dict[str, Any] = {"user_id": principal.user_id, "role": principal.role.value, "status": status}
            docs = await self._approvals.list_for(**kwargs, skip=skip, limit=limit)
            total = await self._approvals.count_for(**kwargs)
        else:
            if principal.role is Role.MANAGER:
                raise AppError(ErrorCode.FORBIDDEN)
            docs = await self._approvals.list_all(status=status, skip=skip, limit=limit)
            total = await self._approvals.count_all(status=status)
        return ApprovalPage(tuple(r for r in map(_readable, docs) if r is not None), total)

    async def decide(self, principal: Principal, approval_id: str, body: ApprovalDecisionRequest) -> ApprovalRecord:
        try:
            decided = await self._decider.decide(
                approval_id,
                decision=body.decision,
                comment=body.comment,
                channel="web",
                user_id=principal.user_id,
                role=principal.role.value,
            )
        except (ApprovalNotFoundError, NotApproverError):
            raise AppError(ErrorCode.NOT_FOUND) from None
        except AlreadyDecidedError:
            raise AppError(ErrorCode.CONFLICT, _ALREADY_DECIDED) from None
        return to_record(decided)
