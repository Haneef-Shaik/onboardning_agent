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
from datetime import UTC, datetime, timedelta
from typing import Final, NoReturn

from onb_contracts.notify import TELEGRAM_SINK, Channel, NotifySendArgs, NotifySendResult
from onb_crypto import (
    Grant,
    GrantAudit,
    GrantAuditRecord,
    GrantRejected,
    GrantVerifier,
    MaskedField,
    RejectReason,
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
from .sends import SendClaims

__all__ = ["NotifySink", "ResolverFactory", "SinkRefusedError"]

_log = logging.getLogger("onb_mcp_mocks.notify")
_VAULT_REF_MARK: Final = "ptk_"
AGENT_KIND: Final = "agent_message"
SWEEP_MARGIN: Final = timedelta(minutes=5)

ResolverFactory = Callable[[GrantAudit], SinkResolver]
"""Builds PII-4's resolver with this call's audit hook (`SinkResolver.create(keyring, store, verifier,
policy, audit)`), so a rejection is recorded against the agent that presented the grant."""


class SinkRefusedError(Exception):
    """The send was refused; nothing was sent. `reason` is a code the tool error carries."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
        self._sends = SendClaims(store)

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
        verified = self._verified(grant, caller)
        audit = CallerGrantAudit(self._auditor, caller)
        if verified is None:  # unverifiable or another org's: the resolver refuses it, and records why
            return await self._resolve_and_send(caller, grant, args, token, None, audit, owned=False)
        if verified.run_id != caller.run_id:
            await self._refuse(caller, verified, audit, RejectReason.WRONG_RUN, "the grant is for another run")
        grant_digest = digest(grant)
        claim = await self._sends.claim(caller, grant_digest, _args_digest(args))
        if claim.status == "sent" and claim.outbox_id is not None:
            return NotifySendResult(
                status="sent", channel=Channel.TELEGRAM, notification_id=claim.outbox_id, replayed=True
            )
        if claim.status == "conflict":
            await self._refuse(caller, verified, audit, RejectReason.REPLAYED, "the grant was already used")
        if claim.status != "owned":  # failed or still pending: the first call reports its own outcome
            raise SinkRefusedError(f"send_{claim.status}", f"send_{claim.status}: an earlier call with this grant")
        return await self._resolve_and_send(caller, grant, args, token, verified, audit, owned=True)

    async def _resolve_and_send(
        self,
        caller: Caller,
        grant: str,
        args: NotifySendArgs,
        token: str,
        verified: Grant | None,
        audit: CallerGrantAudit,
        *,
        owned: bool,
    ) -> NotifySendResult:
        grant_digest = digest(grant)
        try:
            values = await self._resolver(audit).resolve_at_sink(
                grant, TELEGRAM_SINK, [SinkToken(VaultField.MOBILE, token)]
            )
        except GrantRejected as rejected:
            if owned:
                await self._sends.release(grant_digest)
            await self._rejected(caller, verified, rejected)
            raise _refusal(rejected.reason) from None
        try:
            outbox_id = await self._deliver(caller, args, token, values[token], verified, grant_digest)
        except Exception:
            # The value was resolved (the jti is spent): record the failure once, so a retry with this
            # grant is refused `send_failed` instead of failing the task a second time.
            await self._sends.mark_failed(grant_digest)
            await self._report(caller, verified, "send_failed")
            raise
        await self._sweep_expired_jtis()
        return NotifySendResult(status="sent", channel=Channel.TELEGRAM, notification_id=outbox_id)

    async def _deliver(
        self, caller: Caller, args: NotifySendArgs, token: str, mobile: str, verified: Grant | None, grant_digest: str
    ) -> str:
        """Send through the transport, then store the masked copy, mark the claim sent and publish
        `notification.sent`, all in one transaction. The raw value goes no further than the transport."""
        run_id, task_id = (verified.run_id, verified.task_id) if verified else (None, None)
        entry = OutboxEntry(
            recipient_token=token,
            recipient_masked=mask(MaskedField.MOBILE, mobile),
            text=args.body,
            run_id=run_id,
            task_id=task_id,
            agent=caller.agent,
            grant_digest=grant_digest,
            args_digest=_args_digest(args),
        )
        sent, document = await self._telegram.send(int(pii_normalize.mobile(mobile)), entry)
        outbox_id = str(document["_id"])

        async def store_copy() -> None:
            await self._telegram.store(document)
            await self._sends.mark_sent(grant_digest, outbox_id)

        payload = {"outbox_id": outbox_id, "channel": "telegram", "run_id": run_id, "task_id": task_id}
        await self._events.sent(payload, key=f"notify:sent:{grant_digest}", run_id=run_id, change=store_copy)
        _log.info("telegram mock sent: org=%s outbox=%s message=%d", caller.org_id, outbox_id, sent.message_id)
        return outbox_id

    def _verified(self, grant: str, caller: Caller) -> Grant | None:
        """The grant's claims when its signature verifies and it is for the caller's org; nothing
        otherwise (the resolver checks it again and alone decides, and another org's run or task is
        never written into the caller's org)."""
        try:
            verified: Grant = self._verifier.verify(grant)
        except GrantRejected:
            return None
        return verified if verified.org_id == caller.org_id else None

    async def _refuse(
        self, caller: Caller, verified: Grant, audit: CallerGrantAudit, reason: RejectReason, detail: str
    ) -> NoReturn:
        """A refusal the sink decides itself (before the resolver): the same records as the resolver's."""
        record = GrantAuditRecord(
            org_id=caller.org_id,
            sink=TELEGRAM_SINK,
            reason=reason,
            detail=detail,
            tokens=(),
            at=self._clock(),
            run_id=verified.run_id,
            task_id=verified.task_id,
            subject_id=verified.subject_id,
            jti=verified.jti,
        )
        try:
            await audit.record(record)
        except Exception as exc:
            _log.error("sink grant audit failed: org=%s error=%s", caller.org_id, type(exc).__name__)
        await self._rejected(caller, verified, GrantRejected(reason, detail))
        raise _refusal(reason)

    async def _rejected(self, caller: Caller, verified: Grant | None, rejected: GrantRejected) -> None:
        """The guardrail decision and the task failure report. The grant audit record is already
        written. Failures here are logged: the refusal stands either way."""
        task_id = verified.task_id if verified else None
        try:
            await record_guardrail(self._auditor, caller, task_id, rejected.reason)
        except Exception as exc:
            _log.error("sink guardrail record failed: org=%s error=%s", caller.org_id, type(exc).__name__)
        await self._report(caller, verified, rejected.reason.value)

    async def _report(self, caller: Caller, verified: Grant | None, reason: str) -> None:
        """`task.sink_rejected`: the orchestrator fails the task."""
        payload = {
            "run_id": caller.run_id,
            "agent": caller.agent,
            "task_id": verified.task_id if verified else None,
            "sink": TELEGRAM_SINK,
            "reason": reason,
        }
        try:
            await self._events.rejected(payload, run_id=caller.run_id)
        except Exception as exc:
            _log.error("sink rejection event failed: org=%s error=%s", caller.org_id, type(exc).__name__)

    async def _sweep_expired_jtis(self) -> None:
        """Rows expired for longer than `SWEEP_MARGIN`, so sink replicas with drifting clocks never
        delete a jti another replica still accepts."""
        try:
            cutoff = self._clock() - SWEEP_MARGIN
            await self._store.tenant(JTI_COLLECTION).delete_many({"expires_at": {"$lt": cutoff}})
        except Exception as exc:  # housekeeping only: never fails a send
            _log.warning("pii_grant_jti sweep failed: %s", type(exc).__name__)


def _refusal(reason: RejectReason) -> SinkRefusedError:
    return SinkRefusedError(f"grant_rejected:{reason.value}", f"grant_rejected ({reason.value}): nothing was sent")
