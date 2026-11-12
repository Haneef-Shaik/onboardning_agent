"""The `notify.approval` consumer: tell the approver an approval waits (PRD FR-H2, U5; HIL-1).

`approval.requested` (published by the orchestrator when it parks a task in `waiting_approval`) names an
approval. The approver, or every user of the approver's role, gets:

* an in-app notification (`notifications`, the Approvals Inbox badge), and
* a Telegram-mock message with **Approve** and **Reject** buttons, addressed to them
  (`audience_user_id`, so a MANAGER's `GET /mock-outbox` shows only their own) and tied to the approval by
  `ref = {type: approval, id}`. A press reaches the platform as `decision.received`, and
  `orchestrator.record_decision` attributes it to that audience user (ADR 0013, ADR 0020).

Only a `pending` approval is announced (one already decided or cancelled has nothing to press), and the same
event delivered again tells nobody twice. The text is built from the approval's codes, titles and masked packet,
and is redacted once more here (defence in depth: the packet came from a model). A mobile number is never involved:
the mock's chat id is derived from the platform user id, and a real adapter needs the chat id from the bot's own
`/start` flow (ADR 0013).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts.approvals import APPROVAL_REF_TYPE, APPROVALS_COLLECTION
from onb_contracts.notify import APPROVAL_REQUESTED, OUTBOX_COLLECTION, Decision
from onb_data import DataStore, current_tenant
from onb_eventbus import Handler, JobContext
from onb_guardrails.pii import redact

from .in_app import USERS_COLLECTION, InAppChannel, MalformedEventError, UnknownRecipientError
from .outbox import DecisionButton, OutboxEntry, TelegramMockChannel

__all__ = [
    "approval_message_body",
    "approval_notify_handler",
    "approval_recipients",
    "ensure_approval_notify_indexes",
    "mock_chat_id",
]

_ID: Final = re.compile(r"appr_[A-Za-z0-9_]{1,64}")
_MAX_ROLE_RECIPIENTS: Final = 20
_MAX_TEXT: Final = 3500  # under Telegram's 4096 with room for the buttons' framing
_MAX_LISTED: Final = 10
IN_APP_KIND: Final = APPROVAL_REQUESTED


def _utc_now() -> datetime:
    return datetime.now(UTC)


def mock_chat_id(org_id: str, user_id: str) -> int:
    """The mock's chat id for a platform user: stable, positive (a user chat), derived from ids only."""
    digest = hashlib.sha256(f"{org_id}\x1f{user_id}".encode()).digest()
    return int.from_bytes(digest[:6], "big") or 1


async def ensure_approval_notify_indexes(store: DataStore) -> None:
    await store.ensure_index(OUTBOX_COLLECTION, [("org_id", 1), ("ref.id", 1), ("audience_user_id", 1)])


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _listed(values: object) -> str:
    items = [str(v) for v in values] if isinstance(values, list) else []
    return ", ".join(items[:_MAX_LISTED]) if items else "none"


def _body(approval: Mapping[str, Any]) -> str:
    packet = approval.get("packet")
    lines = [
        f"Approval needed: {approval.get('title', 'an approval')}",
        *_why(approval),
        f"Hire: {approval.get('hire_id')} · run {approval.get('run_id')}",
        f"Items: {_listed((approval.get('request') or {}).get('items'))}",
    ]
    if isinstance(packet, Mapping):
        lines += [
            f"Summary: {packet.get('summary', '')}",
            f"AI recommendation: {packet.get('recommendation', '')}",
            f"Risk flags: {_listed(packet.get('risk_flags'))}",
            f"Policies cited: {_listed(packet.get('cited_policies'))}",
        ]
    else:
        lines.append("No AI summary is available: please review the request yourself.")
    return redact(_clip("\n".join(lines), _MAX_TEXT))


def _why(approval: Mapping[str, Any]) -> list[str]:
    """What approving means, from the record's own fields (never from the agent's text)."""
    if approval.get("kind") == "tool_call":
        rule = approval.get("rule")
        return [f"An agent's call was refused: approving grants the rule {rule} for the whole task."]
    if approval.get("kind") == "needs_human":
        return ["The agent asked for a human: approving lets the task go on."]
    return []


def _short(approval: Mapping[str, Any]) -> str:
    kind = {"tool_call": f" (grants {approval.get('rule')})", "needs_human": " (agent asked for a human)"}
    title = approval.get("title", "an approval")
    return redact(
        f"Approval needed: {title}{kind.get(str(approval.get('kind')), '')} (hire {approval.get('hire_id')})."
    )


def _approval_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("approval_id")
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MalformedEventError("approval_id must be an approval id")
    return value


async def _recipients(store: DataStore, approval: Mapping[str, Any]) -> list[str]:
    users = store.tenant(USERS_COLLECTION)
    if approval.get("approver_id"):
        found = await users.find_one({"_id": approval["approver_id"]}, projection={"_id": 1})
        return [str(found["_id"])] if found else []  # an approver who left the org: nobody to tell
    if approval.get("approver_role"):
        role_users = await users.find({"role": approval["approver_role"]}, limit=_MAX_ROLE_RECIPIENTS)
        return [str(u["_id"]) for u in role_users]
    return []


approval_message_body = _body
"""The approval's Telegram text (HIL-2's reminder repeats it)."""
approval_recipients = _recipients
"""Who decides the approval: its approver, or the users of its approver role (HIL-2's reminder goes to them)."""


def approval_notify_handler(
    store: DataStore, telegram: TelegramMockChannel, clock: Callable[[], datetime] = _utc_now
) -> Handler:
    """The `notify.approval` consumer. The bus has already set the job's tenant scope."""
    in_app = InAppChannel(store)
    outbox = store.tenant(OUTBOX_COLLECTION)

    async def tell(approval: Mapping[str, Any], user_id: str, job: JobContext) -> None:
        approval_id = str(approval["_id"])
        ref = {"type": APPROVAL_REF_TYPE, "id": approval_id}
        try:
            await in_app.deliver(
                user_id,
                kind=IN_APP_KIND,
                body=_short(approval),
                ref=ref,
                run_id=str(approval["run_id"]),
                agent="orchestrator",
                dedupe_key=f"{job.event_id}:{user_id}",
            )
        except UnknownRecipientError:
            return
        first_message = {"ref.type": APPROVAL_REF_TYPE, "ref.id": approval_id, "audience_user_id": user_id}
        if await outbox.find_one({**first_message, "sla_stage": {"$exists": False}}):  # HIL-2's are not this one
            return  # this approver was already messaged for this approval (a redelivery)
        entry = OutboxEntry(
            recipient_token=None,
            recipient_masked=f"user:{user_id}",
            text=_body(approval),
            run_id=str(approval["run_id"]),
            task_id=str(approval["task_id"]),
            agent="orchestrator",
            ref=ref,
            audience_user_id=user_id,
        )
        _, document = await telegram.send(
            mock_chat_id(current_tenant().org_id, user_id),
            entry,
            buttons=[DecisionButton("Approve", Decision.APPROVE), DecisionButton("Reject", Decision.REJECT)],
        )
        await telegram.store(document)

    async def handle(job: JobContext) -> None:
        approval = await store.tenant(APPROVALS_COLLECTION).find_one({"_id": _approval_id(job.payload)})
        if approval is None or approval.get("status") != "pending":
            return  # not ours, or nothing left to press
        for user_id in await _recipients(store, approval):
            await tell(approval, user_id, job)

    return handle
