"""The `notify.approval_sla` consumer: an overdue approval's reminder and escalation (PRD FR-H4; HIL-2, ADR 0027).

`approval.overdue {approval_id, run_id, task_id, stage, escalate_to}` comes from the orchestrator's SLA sweep, once
per approval and stage. While the approval is still `pending`:

* `reminder`: the approver (or every user of the approver role) gets an in-app note (`approval.reminder`) and a
  Telegram-mock message that starts "Reminder: this approval is overdue", with the same Approve/Reject buttons as
  the first message (a press is attributed to that audience user, ADR 0020);
* `escalation`: every user of the `escalate_to` role (HR or HR_ADMIN) gets an in-app note (`approval.escalated`)
  and a Telegram-mock message naming whom the approval waits on, **without** buttons: HR is told, the approver
  still decides.

Each part is deduplicated on its own (the in-app note by event and user, the message by approval, user and stage),
so a redelivered job tells nobody twice and a job that died half way finishes the rest. The text is built from
the record's codes and titles and redacted once more here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from onb_contracts.approvals import APPROVAL_REF_TYPE, APPROVALS_COLLECTION
from onb_contracts.auth import Role
from onb_contracts.notify import OUTBOX_COLLECTION, Decision
from onb_data import DataStore, current_tenant
from onb_eventbus import Handler, JobContext
from onb_guardrails.pii import redact

from .approvals import approval_message_body, approval_recipients, mock_chat_id
from .in_app import USERS_COLLECTION, InAppChannel, MalformedEventError, UnknownRecipientError
from .outbox import DecisionButton, OutboxEntry, TelegramMockChannel

__all__ = ["ESCALATED_KIND", "REMINDER_KIND", "approval_sla_handler"]

REMINDER_KIND: Final = "approval.reminder"
ESCALATED_KIND: Final = "approval.escalated"
_ID: Final = re.compile(r"appr_[A-Za-z0-9_]{1,64}")
_STAGES: Final = frozenset({"reminder", "escalation"})
_ESCALATION_ROLES: Final = frozenset({Role.HR.value, Role.HR_ADMIN.value})
_MAX_ROLE_RECIPIENTS: Final = 20
_BUTTONS: Final = (DecisionButton("Approve", Decision.APPROVE), DecisionButton("Reject", Decision.REJECT))


def _event(payload: Mapping[str, Any]) -> tuple[str, str, str | None]:
    approval_id, stage, role = payload.get("approval_id"), payload.get("stage"), payload.get("escalate_to")
    if not isinstance(approval_id, str) or not _ID.fullmatch(approval_id):
        raise MalformedEventError("approval_id must be an approval id")
    if stage not in _STAGES:
        raise MalformedEventError("stage must be reminder or escalation")
    if stage == "escalation" and role not in _ESCALATION_ROLES:
        raise MalformedEventError("an escalation names the HR role it goes to")
    return approval_id, str(stage), str(role) if stage == "escalation" else None


def _waits_on(approval: Mapping[str, Any]) -> str:
    if approval.get("approver_id"):
        return f"user {approval['approver_id']}"
    return f"the {approval.get('approver_role')} role"


def _reminder_text(approval: Mapping[str, Any]) -> str:
    return redact(f"Reminder: this approval is overdue (SLA {approval.get('sla_hours') or 'default'} h).\n") + (
        approval_message_body(approval)
    )


def _escalation_text(approval: Mapping[str, Any]) -> str:
    lines = [
        "Escalated: an approval is overdue and still undecided.",
        f"Approval: {approval.get('title', 'an approval')}",
        f"Waiting on: {_waits_on(approval)}",
        f"Hire: {approval.get('hire_id')} · run {approval.get('run_id')} · task {approval.get('task_id')}",
        f"Requested: {approval.get('requested_at')} · SLA {approval.get('sla_hours') or 'default'} h",
    ]
    return redact("\n".join(lines))


def _note(approval: Mapping[str, Any], stage: str) -> str:
    title = approval.get("title", "an approval")
    if stage == "reminder":
        return redact(f"Overdue approval: {title} (hire {approval.get('hire_id')}). Please decide.")
    return redact(f"Escalated: {title} (hire {approval.get('hire_id')}) is overdue; it waits on {_waits_on(approval)}.")


async def _role_users(store: DataStore, role: str) -> list[str]:
    users = await store.tenant(USERS_COLLECTION).find({"role": role}, limit=_MAX_ROLE_RECIPIENTS)
    return [str(u["_id"]) for u in users]


def approval_sla_handler(store: DataStore, telegram: TelegramMockChannel) -> Handler:
    """The `notify.approval_sla` consumer. The bus has already set the job's tenant scope."""
    in_app = InAppChannel(store)
    outbox = store.tenant(OUTBOX_COLLECTION)

    async def tell(approval: Mapping[str, Any], user_id: str, stage: str, job: JobContext) -> None:
        approval_id = str(approval["_id"])
        ref = {"type": APPROVAL_REF_TYPE, "id": approval_id}
        try:
            await in_app.deliver(
                user_id,
                kind=REMINDER_KIND if stage == "reminder" else ESCALATED_KIND,
                body=_note(approval, stage),
                ref=ref,
                run_id=str(approval["run_id"]),
                agent="orchestrator",
                dedupe_key=f"{job.event_id}:{user_id}",
            )
        except UnknownRecipientError:
            return
        sent_before = {"ref.type": APPROVAL_REF_TYPE, "ref.id": approval_id, "audience_user_id": user_id}
        if await outbox.find_one({**sent_before, "sla_stage": stage}):
            return  # this stage was already messaged to this user (a redelivery)
        entry = OutboxEntry(
            recipient_token=None,
            recipient_masked=f"user:{user_id}",
            text=_reminder_text(approval) if stage == "reminder" else _escalation_text(approval),
            run_id=str(approval["run_id"]),
            task_id=str(approval["task_id"]),
            agent="orchestrator",
            ref=ref,
            audience_user_id=user_id,
        )
        buttons: Sequence[DecisionButton] = _BUTTONS if stage == "reminder" else ()
        _, document = await telegram.send(mock_chat_id(current_tenant().org_id, user_id), entry, buttons=buttons)
        await telegram.store({**document, "sla_stage": stage})

    async def handle(job: JobContext) -> None:
        approval_id, stage, role = _event(job.payload)
        approval = await store.tenant(APPROVALS_COLLECTION).find_one({"_id": approval_id})
        if approval is None or approval.get("status") != "pending":
            return  # not this org's, or decided meanwhile: nothing to chase
        recipients = await (approval_recipients(store, approval) if role is None else _role_users(store, role))
        for user_id in recipients:
            await tell(approval, user_id, stage, job)

    return handle
