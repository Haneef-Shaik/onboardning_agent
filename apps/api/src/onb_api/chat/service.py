"""The Run Assistant chat service (PRD FR-C1, FR-C2, U6, §7.10; CHAT-1).

**Who may ask.** The run is looked up with the same rule as `GET /runs/{id}` (`RunControl.visible_run`):
HR and HR_ADMIN for any run of their org, a MANAGER only for a hire they manage. Anything else is 404 before
the assistant is called and before anything is stored, so a manager cannot learn that another manager's
run exists (FR-C1).

**What is stored.** The question and the answer, each redacted through the PII layer (`onb_guardrails.pii`)
first: a stored message holds typed markers such as `[PAN]`, never the raw value. The assistant also gets
only the redacted question.

**What the assistant may do.** Answer. It never changes a run. If it proposes a retry the service checks the
proposal against the run (the run is blocked, the task is failed, the run is the one in this chat, the asker
is HR), stores it as `pending` and returns its id; the assistant's own words never retry anything. The retry
runs only on `confirm`: by the same user, in the same run, once, before it expires, through
`RunControl.retry_task`, which audits it like a retry made on the run page (FR-C2).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from onb_agents.runs import RunState, TaskState
from onb_contracts import ErrorCode
from onb_contracts.auth import Role
from onb_contracts.chat import ChatMessageRecord, Citation, ProposalRecord
from onb_contracts.runs import RunRecord
from onb_guardrails.pii import redact

from onb_api.auth.tokens import Principal
from onb_api.chat.assistant import AssistantAnswer, AssistantTurn, RunAssistant
from onb_api.chat.store import ChatStore
from onb_api.errors import AppError
from onb_api.runs.control import RunControl

__all__ = ["CONFIRM_REASON", "DEFAULT_PROPOSAL_TTL", "ChatService"]

CONFIRM_REASON: Final = "Retry confirmed in the Run Assistant chat"
DEFAULT_PROPOSAL_TTL: Final = timedelta(minutes=15)
PROPOSABLE_ROLES: Final = frozenset({Role.HR, Role.HR_ADMIN})
RETRY: Final = "retry_task"
_log = logging.getLogger("onb_api.chat")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ChatService:
    def __init__(
        self,
        *,
        store: ChatStore,
        control: RunControl,
        assistant: RunAssistant,
        clock: Callable[[], datetime] = _utc_now,
        proposal_ttl: timedelta = DEFAULT_PROPOSAL_TTL,
    ) -> None:
        self._store = store
        self._control = control
        self._assistant = assistant
        self._clock = clock
        self._ttl = proposal_ttl

    async def ensure_indexes(self) -> None:
        await self._store.ensure_indexes()

    # --- ask -----------------------------------------------------------------------------------

    async def chat(self, principal: Principal, run_id: str, message: str) -> ChatMessageRecord:
        await self._control.visible_run(principal, run_id)  # 404 for a run this caller may not see
        now = self._clock()
        question = redact(message)
        conversation = await self._store.conversation(run_id, principal.user_id, at=now)
        await self._store.add_message(conversation, role="user", content=question, at=now)
        turn = AssistantTurn(
            org_id=principal.org_id,
            run_id=run_id,
            user_id=principal.user_id,
            role=principal.role.value,
            message=question,
            conversation_id=conversation["_id"],
        )
        try:
            given = await self._assistant.answer(turn)
        except Exception:
            _log.exception("run assistant failed", extra={"run_id": run_id})
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "The Run Assistant could not answer; try again") from None
        tasks = {task["task_id"]: task for task in await self._control.store.tasks(run_id)}
        citations = self._citations(given, run_id, tasks)
        proposal = await self._proposal(principal, conversation, given, run_id, tasks, now)
        stored = await self._store.add_message(
            conversation,
            role="assistant",
            content=redact(given.answer),
            at=self._clock(),
            status=given.status,
            citations=citations,
            proposal_id=None if proposal is None else proposal["_id"],
        )
        return self._record(stored, proposal)

    @staticmethod
    def _citations(given: AssistantAnswer, run_id: str, tasks: Mapping[str, Any]) -> list[tuple[str, str]]:
        """Only tasks of this run that exist: a citation the model made up is dropped, never linked."""
        seen: list[tuple[str, str]] = []
        for cited_run, task_id in given.citations:
            if cited_run == run_id and task_id in tasks and (cited_run, task_id) not in seen:
                seen.append((cited_run, task_id))
        return seen

    async def _proposal(
        self,
        principal: Principal,
        conversation: Mapping[str, Any],
        given: AssistantAnswer,
        run_id: str,
        tasks: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        proposed = given.proposal
        if given.status != "ok" or proposed is None or proposed.type != RETRY:
            return None
        if principal.role not in PROPOSABLE_ROLES or proposed.run_id != run_id or proposed.task_id is None:
            return None
        task = tasks.get(proposed.task_id)
        run = await self._control.store.get(run_id)
        if (
            task is None
            or run is None
            or task["status"] != TaskState.FAILED.value
            or run["status"] != RunState.BLOCKED.value
        ):
            return None
        return await self._store.add_proposal(
            conversation, type=RETRY, task_id=proposed.task_id, at=now, expires_at=now + self._ttl
        )

    # --- confirm -------------------------------------------------------------------------------

    async def confirm(self, principal: Principal, run_id: str, proposal_id: str) -> RunRecord:
        """Run the proposed retry, once, if this user was offered it in this run's chat and it is still open."""
        await self._control.visible_run(principal, run_id)
        proposal = await self._store.proposal(proposal_id)
        if proposal is None or proposal["run_id"] != run_id or proposal["user_id"] != principal.user_id:
            raise AppError(ErrorCode.NOT_FOUND)  # someone else's proposal looks like none at all
        if not await self._store.claim(proposal_id, at=self._clock()):
            raise AppError(ErrorCode.CONFLICT, "This proposal was already used or has expired")
        try:
            return await self._control.retry_task(principal, run_id, proposal["task_id"], CONFIRM_REASON)
        except AppError:
            await self._store.mark_failed(proposal_id)
            raise

    # --- read ----------------------------------------------------------------------------------

    async def history(self, principal: Principal, run_id: str) -> list[ChatMessageRecord]:
        await self._control.visible_run(principal, run_id)
        conversation = await self._store.find_conversation(run_id, principal.user_id)
        if conversation is None:
            return []
        stored = await self._store.messages(conversation["_id"])
        proposals = await self._store.proposals([m["proposal_id"] for m in stored if m.get("proposal_id")])
        return [self._record(m, proposals.get(m.get("proposal_id") or "")) for m in stored]

    def _record(self, message: Mapping[str, Any], proposal: Mapping[str, Any] | None) -> ChatMessageRecord:
        return ChatMessageRecord(
            id=message["_id"],
            role=message["role"],
            content=message["content"],
            status=message.get("status"),
            citations=[Citation(run_id=c["run_id"], task_id=c["task_id"]) for c in message.get("citations", ())],
            proposal=None if proposal is None else self._proposal_record(proposal),
            created_at=message["created_at"],
        )

    def _proposal_record(self, proposal: Mapping[str, Any]) -> ProposalRecord:
        status = proposal["status"]
        if status == "pending" and _as_utc(proposal["expires_at"]) <= self._clock():
            status = "failed"  # expired: shown as no longer confirmable
        return ProposalRecord(
            proposal_id=proposal["_id"],
            type=proposal["type"],
            run_id=proposal["run_id"],
            task_id=proposal["task_id"],
            status=status,
            expires_at=_as_utc(proposal["expires_at"]),
        )
