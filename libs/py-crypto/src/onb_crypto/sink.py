"""Resolving PII tokens at a sink (PRD §7.10 sink flow, FR-P5, §8.1 Tool).

    verify grant → org, sink, granted field, field → sink policy → consume jti
      → vault: token binding (org, subject, field) + record signature → decrypt
      → write one masked `pii_access_log` entry per value → release

`resolve_at_sink` releases every value or none. Any refusal, and any store failure, raises
`GrantRejected` with a `RejectReason` and emits one `GrantAuditRecord`; nothing is logged to
`pii_access_log` and no value leaves. A grant's `jti` is single-use: it is recorded in
`pii_grant_jti` with the grant's expiry, and any later use is `REPLAYED` (after expiry the grant is
`EXPIRED` anyway, so expired rows may be deleted). Run `ensure_grant_indexes(store)` at startup:
concurrent replays have exactly one winner only with its unique index.

Every call runs inside `tenant_scope(org_id)`, and the grant must be for that org.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol

from onb_data import DataStore, current_tenant
from pymongo.errors import DuplicateKeyError

from onb_crypto.errors import (
    CryptoError,
    SupersededTokenError,
    TokenBindingError,
    TokenNotFoundError,
    VaultIntegrityError,
)
from onb_crypto.grants import Grant, GrantRejected, GrantVerifier, RejectReason
from onb_crypto.keyring import Keyring
from onb_crypto.masks import MaskedField, mask
from onb_crypto.sink_policy import SinkPolicy
from onb_crypto.tokens import is_token
from onb_crypto.vault import PURGED, Vault, VaultRepository
from onb_crypto.vault_record import SubjectType, VaultField

__all__ = [
    "ACCESS_LOG_COLLECTION",
    "JTI_COLLECTION",
    "GrantAudit",
    "GrantAuditRecord",
    "SinkRepository",
    "SinkResolver",
    "SinkStore",
    "SinkToken",
    "ensure_grant_indexes",
]

log = logging.getLogger(__name__)

ACCESS_LOG_COLLECTION: Final = "pii_access_log"
JTI_COLLECTION: Final = "pii_grant_jti"
MAX_TOKENS_PER_CALL: Final = 32
_ACTION: Final = "pii.grant.resolve"
_UNMASKABLE: Final = "[masked]"
_MASKED_FIELDS: Final = {f.value: f for f in MaskedField}


@dataclass(frozen=True, slots=True)
class SinkToken:
    """A token the sink was handed, with the field the sink uses it as (a telegram recipient is a mobile)."""

    field: VaultField
    token: str


@dataclass(frozen=True, slots=True)
class GrantAuditRecord:
    """A refused resolution. The grant's claims appear only when its signature verified; never a value."""

    org_id: str
    sink: str
    reason: RejectReason
    detail: str
    tokens: tuple[str, ...]
    at: datetime
    run_id: str | None = None
    task_id: str | None = None
    subject_id: str | None = None
    jti: str | None = None
    action: str = _ACTION
    outcome: Literal["rejected"] = "rejected"
    actor_type: str = "service"

    def to_document(self) -> dict[str, Any]:
        """The AUD-1 audit shape: `{actor, action, target, outcome, reason, at}` (org added by the repo)."""
        return {
            "actor": {"type": self.actor_type, "id": self.sink},
            "action": self.action,
            "target": {
                "type": "pii_grant",
                "id": self.jti,
                "run_id": self.run_id,
                "task_id": self.task_id,
                "subject_id": self.subject_id,
                "tokens": list(self.tokens),
            },
            "outcome": self.outcome,
            "reason": self.reason.value,
            "detail": self.detail,
            "at": self.at,
        }


class SinkRepository(VaultRepository, Protocol):
    """The part of T-1's `TenantRepository` the sink uses."""

    async def insert_many(self, documents: Sequence[Mapping[str, Any]]) -> list[Any]: ...


class SinkStore(Protocol):
    """T-1's `DataStore`: tenant repositories only."""

    def tenant(self, collection: str) -> SinkRepository: ...


class GrantAudit(Protocol):
    """Where rejections go: the audit log (AUD-1), and the guardrail event stream (NOTIF-1 wires it)."""

    async def record(self, record: GrantAuditRecord) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reject(reason: RejectReason, detail: str) -> GrantRejected:
    return GrantRejected(reason, detail)


def _masked(field: VaultField, value: str) -> str:
    masked_field = _MASKED_FIELDS.get(field.value)
    if masked_field is None:
        return _UNMASKABLE
    try:
        return mask(masked_field, value)
    except ValueError:
        return _UNMASKABLE


def _checked_tokens(tokens: object) -> tuple[SinkToken, ...]:
    if not isinstance(tokens, list | tuple) or not tokens or len(tokens) > MAX_TOKENS_PER_CALL:
        raise _reject(RejectReason.MALFORMED, f"a sink resolves 1-{MAX_TOKENS_PER_CALL} tokens per call")
    if not all(isinstance(t, SinkToken) and isinstance(t.field, VaultField) and is_token(t.token) for t in tokens):
        raise _reject(RejectReason.MALFORMED, "every token must be a SinkToken holding a ptk_ token")
    if len({t.token for t in tokens}) != len(tokens):
        raise _reject(RejectReason.MALFORMED, "a token is presented twice")
    return tuple(tokens)


def _auditable_tokens(tokens: object) -> tuple[str, ...]:
    """Only well-formed `ptk_` tokens go into an audit record: whatever else was presented may be a raw value."""
    if not isinstance(tokens, list | tuple):
        return ()
    found = [t.token for t in tokens[:MAX_TOKENS_PER_CALL] if isinstance(t, SinkToken) and is_token(t.token)]
    return tuple(dict.fromkeys(found))


_VAULT_REASONS: Final[tuple[tuple[type[CryptoError], RejectReason], ...]] = (
    (TokenNotFoundError, RejectReason.NOT_FOUND),
    (TokenBindingError, RejectReason.NOT_BOUND),
    (SupersededTokenError, RejectReason.SUPERSEDED),
    (VaultIntegrityError, RejectReason.RECORD_INTEGRITY),
)


class SinkResolver:
    """The only way a sink turns tokens into values. Holds public grant keys, never the signing key."""

    def __init__(
        self,
        vault: Vault,
        verifier: GrantVerifier,
        policy: SinkPolicy,
        store: SinkStore,
        audit: GrantAudit,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._vault = vault
        self._verifier = verifier
        self._policy = policy
        self._store = store
        self._audit = audit
        self._clock = clock

    @classmethod
    def create(
        cls,
        keyring: Keyring,
        store: SinkStore,
        verifier: GrantVerifier,
        policy: SinkPolicy,
        audit: GrantAudit,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> SinkResolver:
        """A resolver over its own read-only use of the vault, so a sink never holds a `Vault` itself."""
        return cls(Vault(keyring, store, clock=clock), verifier, policy, store, audit, clock=clock)

    async def resolve_at_sink(self, grant: str, sink_id: str, tokens: Sequence[SinkToken]) -> dict[str, str]:
        """`{token: value}` for every token, or `GrantRejected` and nothing.

        The caller's tenant scope must come from its own signed context (the agent token the MCP hub
        verified), never from the grant: the grant's org is checked against it (`WRONG_ORG`)."""
        org_id = current_tenant().org_id
        verified: Grant | None = None
        try:
            try:
                verified = self._verifier.verify(grant)
                return await self._resolve(verified, org_id, sink_id, _checked_tokens(tokens))
            except GrantRejected:
                raise
            except Exception as error:  # a store or clock failure: fail closed as a rejection, cause logged
                log.error("grant resolution failed: org=%s sink=%s error=%s", org_id, sink_id, type(error).__name__)
                raise _reject(RejectReason.VAULT_ERROR, "the resolution could not complete") from None
        except GrantRejected as rejected:
            await self._record_rejection(org_id, sink_id, rejected, verified, _auditable_tokens(tokens))
            raise

    async def _resolve(self, grant: Grant, org_id: str, sink_id: str, tokens: tuple[SinkToken, ...]) -> dict[str, str]:
        if grant.org_id != org_id:
            raise _reject(RejectReason.WRONG_ORG, f"grant {grant.jti} is for another org")
        if grant.sink != sink_id:
            raise _reject(RejectReason.WRONG_SINK, f"grant {grant.jti} is not for sink {sink_id}")
        for presented in tokens:
            if presented.field not in grant.fields:
                raise _reject(RejectReason.FIELD_NOT_GRANTED, f"grant {grant.jti} does not grant {presented.field}")
            if not self._policy.allows(presented.field, sink_id):
                raise _reject(
                    RejectReason.FIELD_FORBIDDEN_BY_POLICY, f"the sink policy forbids {presented.field} at {sink_id}"
                )
        await self._consume_jti(grant)
        values = {presented.token: await self._value(grant, presented) for presented in tokens}
        await self._log_access(grant, tokens, values)
        log.info("grant resolved: org=%s jti=%s sink=%s tokens=%d", org_id, grant.jti, sink_id, len(values))
        return values

    async def _value(self, grant: Grant, presented: SinkToken) -> str:
        try:
            value = await self._vault.resolve(
                presented.token, org_id=grant.org_id, subject_id=grant.subject_id, field=presented.field
            )
        except CryptoError as error:
            reason = next((r for kind, r in _VAULT_REASONS if isinstance(error, kind)), RejectReason.VAULT_ERROR)
            raise _reject(reason, f"token {presented.token} refused by the vault") from None
        if value == PURGED:
            raise _reject(RejectReason.PURGED, f"token {presented.token} was purged")
        return value

    async def _consume_jti(self, grant: Grant) -> None:
        """Single use: the first resolution keeps the jti until the grant expires; any later one is a replay."""
        repo = self._store.tenant(JTI_COLLECTION)
        if await repo.find({"jti": grant.jti}, limit=1):
            raise _reject(RejectReason.REPLAYED, f"grant {grant.jti} was already used")
        try:
            await repo.insert_one(
                {
                    "jti": grant.jti,
                    "run_id": grant.run_id,
                    "task_id": grant.task_id,
                    "sink": grant.sink,
                    "expires_at": grant.expires_at,
                    "used_at": self._clock(),
                }
            )
        except DuplicateKeyError:
            raise _reject(RejectReason.REPLAYED, f"grant {grant.jti} was used concurrently") from None

    async def _log_access(self, grant: Grant, tokens: tuple[SinkToken, ...], values: Mapping[str, str]) -> None:
        now = self._clock()
        entries = [
            {
                "kind": "resolve",
                "actor": {"type": "service", "id": grant.sink},
                "sink": grant.sink,
                "run_id": grant.run_id,
                "task_id": grant.task_id,
                "jti": grant.jti,
                "subject": {"type": SubjectType.NEW_HIRE.value, "id": grant.subject_id},
                "field": presented.field.value,
                "token": presented.token,
                "masked": _masked(presented.field, values[presented.token]),
                "at": now,
            }
            for presented in tokens
        ]
        await self._store.tenant(ACCESS_LOG_COLLECTION).insert_many(entries)

    async def _record_rejection(
        self, org_id: str, sink_id: str, rejected: GrantRejected, grant: Grant | None, tokens: tuple[str, ...]
    ) -> None:
        log.warning(
            "grant rejected: org=%s sink=%s reason=%s jti=%s",
            org_id,
            sink_id,
            rejected.reason.value,
            grant.jti if grant else "(unverified)",
        )
        record = GrantAuditRecord(
            org_id=org_id,
            sink=sink_id if isinstance(sink_id, str) else "(invalid)",
            reason=rejected.reason,
            detail=rejected.detail,
            tokens=tokens,
            at=self._clock(),
            run_id=grant.run_id if grant else None,
            task_id=grant.task_id if grant else None,
            subject_id=grant.subject_id if grant else None,
            jti=grant.jti if grant else None,
        )
        try:
            await self._audit.record(record)
        except Exception:
            # The rejection still stands and is re-raised by the caller; the audit gap is logged loudly.
            log.exception("grant rejection could not be audited: org=%s sink=%s", org_id, sink_id)


async def ensure_grant_indexes(store: DataStore) -> None:
    """A jti is unique per org, so two concurrent uses of one grant have exactly one winner."""
    await store.ensure_index(JTI_COLLECTION, [("org_id", 1), ("jti", 1)], unique=True)
    await store.ensure_index(JTI_COLLECTION, [("org_id", 1), ("expires_at", 1)])
    await store.ensure_index(ACCESS_LOG_COLLECTION, [("org_id", 1), ("subject.id", 1), ("at", -1)])
