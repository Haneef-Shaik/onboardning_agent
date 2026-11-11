"""`notify_send` at the sink (PRD §7.10):

    telegram:  verify grant → verify token binding → verify vault record signature → decrypt
               → "send" (MOCK-1 transport) → store a masked copy only (+ `notification.sent`)
               → log to pii_access_log (PII-4's resolver)
    in_app:    the recipient must be a user of the caller's org → `notifications`

The org, agent and run come from the hub-signed call context (`Caller`), never from the grant or the
arguments. A refused grant (any `GrantRejected`) sends nothing and stores nothing in `mock_outbox`,
writes the grant audit record and a guardrail decision (audit + `guardrail.outcome` span event), and
publishes `task.sink_rejected` so the orchestrator fails the task; the call answers
`SinkRefusedError(grant_rejected:<reason>)`.

The sink's own work after a resolution also sweeps this org's expired `pii_grant_jti` rows: a jti
is only needed until its grant expires (an expired grant is refused `expired` before its jti is
read), so the collection holds at most the org's live grants plus what expired since its last send.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from onb_contracts.notify import TELEGRAM_SINK, Channel, NotifySendArgs, NotifySendResult
from onb_crypto import (
    Grant,
    GrantAudit,
    GrantRejected,
    GrantVerifier,
    MaskedField,
    SinkResolver,
    SinkToken,
    VaultField,
    mask,
)
from onb_crypto import normalize as pii_normalize
from onb_crypto.sink import JTI_COLLECTION
from onb_data import Auditor, DataStore

from .audit import Caller, CallerGrantAudit, record_guardrail
from .events import NotifyEvents
from .in_app import InAppChannel
from .outbox import OutboxEntry, TelegramMockChannel, digest

__all__ = ["NotifySink", "ResolverFactory", "SinkRefusedError"]

_log = logging.getLogger("onb_mcp_mocks.notify")
_VAULT_REF_MARK: Final = "ptk_"
AGENT_KIND: Final = "agent_message"

ResolverFactory = Callable[[GrantAudit], SinkResolver]
"""Builds PII-4's resolver with this call's audit hook (`SinkResolver.create(keyring, store, verifier,
policy, audit)`), so a rejection is recorded against the agent that presented the grant."""


class SinkRefusedError(Exception):
    """The send was refused; nothing was sent. `reason` is a code the tool error carries."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Claims:
    run_id: str | None
    task_id: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _args_digest(args: NotifySendArgs) -> str:
    return digest(json.dumps(args.model_dump(mode="json"), sort_keys=True))


class NotifySink:
    def __init__(
        self,
        store: DataStore,
        *,
        resolver: ResolverFactory,
        verifier: GrantVerifier,
        telegram: TelegramMockChannel,
        events: NotifyEvents,
        auditor: Auditor,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._verifier = verifier
        self._telegram = telegram
        self._in_app = InAppChannel(store)
        self._events = events
        self._auditor = auditor
        self._clock = clock

    async def send(self, caller: Caller, grant: str | None, args: NotifySendArgs) -> NotifySendResult:
        """Run inside `tenant_scope(caller.org_id)`."""
        if args.channel is Channel.IN_APP:
            return await self._send_in_app(caller, args)
        return await self._send_telegram(caller, grant or "", args)

    async def _send_in_app(self, caller: Caller, args: NotifySendArgs) -> NotifySendResult:
        if _VAULT_REF_MARK in args.body:
            raise SinkRefusedError("invalid_arguments", "invalid arguments: body (vault tokens are not allowed)")
        if args.recipient_user_id is None:  # the contract requires it; checked again for the type
            raise SinkRefusedError("invalid_arguments", "invalid arguments: recipient_user_id")
        try:
            notification_id, _ = await self._in_app.deliver(
                args.recipient_user_id, kind=AGENT_KIND, body=args.body, run_id=caller.run_id, agent=caller.agent
            )
        except LookupError:
            raise SinkRefusedError("unknown_recipient", "unknown_recipient: no such user") from None
        return NotifySendResult(status="sent", channel=Channel.IN_APP, notification_id=notification_id)

    async def _send_telegram(self, caller: Caller, grant: str, args: NotifySendArgs) -> NotifySendResult:
        if _VAULT_REF_MARK in args.body:
            raise SinkRefusedError("invalid_arguments", "invalid arguments: body (vault tokens are not allowed)")
        token = args.recipient_token
        if token is None:  # the contract requires it; checked again for the type
            raise SinkRefusedError("invalid_arguments", "invalid arguments: recipient_token")
        grant_digest, args_digest = digest(grant), _args_digest(args)
        replay = await self._telegram.find_replay(grant_digest) if grant else None
        if replay is not None and replay.get("args_digest") == args_digest:
            return NotifySendResult(
                status="sent", channel=Channel.TELEGRAM, notification_id=str(replay["_id"]), replayed=True
            )
        claims = self._claims(grant)
        audit = CallerGrantAudit(self._auditor, caller)
        try:
            values = await self._resolver(audit).resolve_at_sink(
                grant, TELEGRAM_SINK, [SinkToken(VaultField.MOBILE, token)]
            )
        except GrantRejected as rejected:
            await self._rejected(caller, claims, rejected)
            raise SinkRefusedError(
                f"grant_rejected:{rejected.reason.value}", f"grant_rejected ({rejected.reason.value}): nothing was sent"
            ) from None
        await self._sweep_expired_jtis()
        mobile = values[token]
        entry = OutboxEntry(
            recipient_token=token,
            recipient_masked=mask(MaskedField.MOBILE, mobile),
            text=args.body,
            run_id=claims.run_id,
            task_id=claims.task_id,
            agent=caller.agent,
            grant_digest=grant_digest,
            args_digest=args_digest,
        )
        sent, document = await self._telegram.send(int(pii_normalize.mobile(mobile)), entry)
        del mobile, values  # the raw value goes no further than the transport

        async def store_copy() -> None:
            await self._telegram.store(document)

        payload = {
            "outbox_id": document["_id"],
            "channel": "telegram",
            "run_id": claims.run_id,
            "task_id": claims.task_id,
        }
        await self._events.sent(payload, key=f"notify:sent:{grant_digest}", run_id=claims.run_id, change=store_copy)
        _log.info("telegram mock sent: org=%s outbox=%s message=%d", caller.org_id, document["_id"], sent.message_id)
        return NotifySendResult(status="sent", channel=Channel.TELEGRAM, notification_id=str(document["_id"]))

    def _claims(self, grant: str) -> _Claims:
        """The grant's run and task when its signature verifies (the resolver checks it again, and
        alone decides); nothing otherwise."""
        try:
            verified: Grant = self._verifier.verify(grant)
        except GrantRejected:
            return _Claims(None, None)
        return _Claims(verified.run_id, verified.task_id)

    async def _rejected(self, caller: Caller, claims: _Claims, rejected: GrantRejected) -> None:
        """The guardrail decision and the task failure report. The grant audit record is already
        written by the resolver's hook. Failures here are logged: the refusal stands either way."""
        try:
            await record_guardrail(self._auditor, caller, claims.task_id, rejected.reason)
        except Exception as exc:
            _log.error("sink guardrail record failed: org=%s error=%s", caller.org_id, type(exc).__name__)
        payload = {
            "run_id": caller.run_id,
            "agent": caller.agent,
            "task_id": claims.task_id,
            "sink": TELEGRAM_SINK,
            "reason": rejected.reason.value,
        }
        try:
            await self._events.rejected(payload, run_id=caller.run_id)
        except Exception as exc:
            _log.error("sink rejection event failed: org=%s error=%s", caller.org_id, type(exc).__name__)

    async def _sweep_expired_jtis(self) -> None:
        try:
            await self._store.tenant(JTI_COLLECTION).delete_many({"expires_at": {"$lt": self._clock()}})
        except Exception as exc:  # housekeeping only: never fails a send
            _log.warning("pii_grant_jti sweep failed: %s", type(exc).__name__)
