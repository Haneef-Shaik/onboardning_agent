"""Hires and their sensitive values (PRD FR-R1 hire part, FR-P1/P2/P4/P5, §7.10; HIRE-1).

- **Create.** Every value is validated first (PII-3), then the email's blind index is checked for a
  duplicate in the org, then each value goes to the vault (Aadhaar with the hire's consent), and the
  hire is stored with tokens, masks and blind indexes only. Free-text notes are stored redacted.
- **Read.** Always masked. A MANAGER sees only hires whose `manager_id` is theirs; any other hire, like
  another org's, is 404.
- **Update** (HR/HR_ADMIN): a new vault version and token; the old one is superseded (FR-P4).
- **Reveal** (HR/HR_ADMIN; Aadhaar HR_ADMIN only): with a reason, one field. The value leaves only
  after its `pii_access_log` entry and its audit record are written (fail closed).

Org keys are made on the first sensitive write of an org (ADR 0008). Logs carry ids, fields and
tokens only; never values, notes or reasons.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts import ErrorCode, ErrorDetail
from onb_contracts.auth import Role
from onb_contracts.hires import (
    REVEAL_TTL_SECONDS,
    HireRecord,
    NewHireRequest,
    PiiAccessRecord,
    PiiField,
    PiiUpdateRequest,
    RevealedValue,
)
from onb_crypto import (
    AadhaarConsent,
    BlindIndexField,
    CryptoError,
    Keyring,
    KeyVersionConflictError,
    NoOrgKeysError,
    TokenNotFoundError,
    Vault,
    ensure_org_keys_index,
    ensure_vault_indexes,
)
from onb_data import Actor, Auditor, AuditOutcome, DataStore
from onb_guardrails.pii import redact

from onb_api.auth.scope import manager_filter, require_in_manager_scope
from onb_api.auth.tokens import Principal
from onb_api.auth.users import UserStore
from onb_api.errors import AppError
from onb_api.hires import fields
from onb_api.hires.access_log import AccessQuery, PiiAccessLog
from onb_api.hires.records import hire_record
from onb_api.hires.store import EmailTakenError, HireStore

__all__ = ["HireService"]

log = logging.getLogger(__name__)

_AADHAAR_REVEAL_ROLES: Final = frozenset({Role.HR_ADMIN})
_EMAIL_TAKEN: Final = "A hire with this email already exists"
_NOT_A_MANAGER: Final = ErrorDetail(field="body.manager_id", issue="Not a manager in this org")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_hire_id() -> str:
    return f"h_{secrets.token_hex(12)}"


def _consent_detail(issue: str) -> ErrorDetail:
    return ErrorDetail(field="body.aadhaar_consent", issue=issue)


@dataclass(frozen=True, slots=True)
class _Written:
    """A value stored in the vault for a hire: what the hire document keeps of it."""

    field: PiiField
    token: str
    masked: str
    index: tuple[str, str] | None  # (index name, blind index)


class HireService:
    def __init__(
        self,
        *,
        store: HireStore,
        vault: Vault,
        access_log: PiiAccessLog,
        auditor: Auditor,
        users: UserStore | None,
        startup: Callable[[], Awaitable[None]],
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self._vault = vault
        self._keyring: Keyring = vault.keyring
        self._access_log = access_log
        self._auditor = auditor
        self._users = users
        self._startup = startup
        self._clock = clock

    @classmethod
    def build(cls, data: DataStore, keyring: Keyring, *, auditor: Auditor, users: UserStore | None) -> HireService:
        """The service over one store: hires, the vault, its keys and the access log share it."""
        store, access_log = HireStore(data), PiiAccessLog(data)

        async def startup() -> None:
            await ensure_org_keys_index(data)
            await ensure_vault_indexes(data)
            await store.ensure_indexes()
            await access_log.ensure_indexes()

        vault = Vault(keyring, data)
        return cls(store=store, vault=vault, access_log=access_log, auditor=auditor, users=users, startup=startup)

    async def ensure_indexes(self) -> None:
        await self._startup()

    # --- create ------------------------------------------------------------------------------------

    async def create(self, principal: Principal, request: NewHireRequest) -> HireRecord:
        values = self._checked_values(request)
        await self._check_manager(request.manager_id)
        org_id = principal.org_id
        await self._org_keys(org_id)
        email_hash = await self._index(org_id, PiiField.EMAIL, values[PiiField.EMAIL])
        if await self.store.email_taken(email_hash):
            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN)
        hire_id = _new_hire_id()
        written: list[_Written] = []
        try:
            for field, value in values.items():
                consent = self._consent(request.aadhaar_consent) if field is PiiField.AADHAAR else None
                written.append(await self._write(org_id, hire_id, field, value, principal.user_id, consent))
            document = self._document(principal, hire_id, request, written)
            await self.store.insert(document)
        except EmailTakenError:
            await self._purge(org_id, hire_id, written)
            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN) from None
        except BaseException:
            await self._purge(org_id, hire_id, written)
            raise
        await self._audit_or_undo(principal, hire_id, written)
        log.info("hire created: org=%s hire=%s fields=%s", org_id, hire_id, ",".join(w.field.value for w in written))
        return hire_record(document)

    def _checked_values(self, request: NewHireRequest) -> dict[PiiField, str]:
        """Every given value, trimmed and validated (PII-3), with the Aadhaar consent rules; all refusals
        at once, naming fields only."""
        given = {PiiField(k): v.strip() for k, v in request.pii.model_dump().items() if v is not None}
        details = [fields.invalid(f, f"body.pii.{f.value}") for f, v in given.items() if not fields.is_valid(f, v)]
        has_aadhaar = PiiField.AADHAAR in given
        if has_aadhaar and request.aadhaar_consent is None:
            details.append(_consent_detail("Aadhaar needs the hire's consent"))
        if not has_aadhaar and request.aadhaar_consent is not None:
            details.append(_consent_detail("Consent is only taken with an Aadhaar"))
        if has_aadhaar and request.alternative_id is not None:
            details.append(
                ErrorDetail(field="body.alternative_id", issue="Give Aadhaar or an alternative ID, not both")
            )
        if details:
            raise AppError(ErrorCode.VALIDATION_ERROR, details=details)
        return given

    async def _check_manager(self, manager_id: str) -> None:
        """A MANAGER of the caller's org. Another org's user answers exactly like nobody."""
        if self._users is None:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
        user = await self._users.get_user(manager_id)
        if user is None or user.role is not Role.MANAGER:
            raise AppError(ErrorCode.VALIDATION_ERROR, details=(_NOT_A_MANAGER,))

    def _document(
        self, principal: Principal, hire_id: str, request: NewHireRequest, written: list[_Written]
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "_id": hire_id,
            "name": request.name.strip(),
            "role": request.role.strip(),
            "location": request.location.strip(),
            "start_date": request.start_date.isoformat(),
            "manager_id": request.manager_id,
            "notes": redact(request.notes),
            "pii": {w.field.value: w.token for w in written},
            "pii_masked": {w.field.value: w.masked for w in written},
            "id_proof": {
                "aadhaar_consent": request.aadhaar_consent is not None,
                "alternative_id": None if request.alternative_id is None else request.alternative_id.value,
            },
            "created_by": principal.user_id,
            "created_at": self._now(),
        }
        document.update(dict(w.index for w in written if w.index is not None))
        return document

    async def _audit_or_undo(self, principal: Principal, hire_id: str, written: list[_Written]) -> None:
        """AUD-1: a create whose audit record failed is not reported done, and is taken back."""
        try:
            await self._audit(principal, "hire.create", f"hire:{hire_id}", AuditOutcome.SUCCESS)
        except BaseException:
            await self.store.delete(hire_id)
            await self._purge(principal.org_id, hire_id, written)
            raise

    # --- read --------------------------------------------------------------------------------------

    async def list_hires(self, principal: Principal, *, page: int, limit: int) -> tuple[list[HireRecord], int]:
        docs, total = await self.store.page(manager_filter(principal), page=page, limit=limit)
        return [hire_record(doc) for doc in docs], total

    async def get(self, principal: Principal, hire_id: str) -> HireRecord:
        return hire_record(await self._visible(principal, hire_id))

    async def _visible(self, principal: Principal, hire_id: str) -> dict[str, Any]:
        document = await self.store.get(hire_id)
        if document is None:
            raise AppError(ErrorCode.NOT_FOUND)
        require_in_manager_scope(principal, document)
        return document

    async def access_log(self, query: AccessQuery, *, page: int, limit: int) -> tuple[list[PiiAccessRecord], int]:
        return await self._access_log.page(query, page=page, limit=limit)

    # --- update (FR-P4) ----------------------------------------------------------------------------

    async def update(
        self, principal: Principal, hire_id: str, field: PiiField, request: PiiUpdateRequest
    ) -> HireRecord:
        value = request.value.strip()
        fields.require_valid(field, value, "body.value")
        if field is PiiField.AADHAAR and request.aadhaar_consent is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, details=(_consent_detail("Aadhaar needs the hire's consent"),))
        if field is not PiiField.AADHAAR and request.aadhaar_consent is not None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, details=(_consent_detail("Consent is only taken with an Aadhaar"),)
            )
        await self._visible(principal, hire_id)
        org_id = principal.org_id
        await self._org_keys(org_id)
        if field is PiiField.EMAIL:
            email_hash = await self._index(org_id, field, value)
            if await self.store.email_taken(email_hash, other_than=hire_id):
                raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN)
        consent = self._consent(request.aadhaar_consent) if field is PiiField.AADHAAR else None
        written = await self._write(org_id, hire_id, field, value, principal.user_id, consent)
        changes: dict[str, Any] = {f"pii.{field.value}": written.token, f"pii_masked.{field.value}": written.masked}
        if written.index is not None:
            changes[written.index[0]] = written.index[1]
        if field is PiiField.AADHAAR:
            changes["id_proof.aadhaar_consent"] = True
        try:
            found = await self.store.set_fields(hire_id, changes)
        except EmailTakenError:
            # Lost a race for the email: the new vault version is withdrawn; the hire keeps its old token.
            await self._purge(org_id, hire_id, [written])
            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN) from None
        if not found:
            await self._purge(org_id, hire_id, [written])
            raise AppError(ErrorCode.NOT_FOUND)
        await self._audit(principal, "pii.update", f"hire:{hire_id}/{field.value}", AuditOutcome.SUCCESS)
        log.info("hire pii updated: org=%s hire=%s field=%s token=%s", org_id, hire_id, field.value, written.token)
        return hire_record(await self._visible(principal, hire_id))

    # --- reveal (FR-P2, FR-P5, §7.10 Reveal) -------------------------------------------------------

    async def reveal(self, principal: Principal, hire_id: str, field: PiiField, reason: str) -> RevealedValue:
        target = f"hire:{hire_id}/{field.value}"
        safe_reason = redact(reason.strip())
        if field is PiiField.AADHAAR and principal.role not in _AADHAAR_REVEAL_ROLES:
            await self._audit(principal, "pii.reveal", target, AuditOutcome.DENIED, "aadhaar reveal needs HR_ADMIN")
            raise AppError(ErrorCode.FORBIDDEN)
        document = await self._visible(principal, hire_id)
        token = document.get("pii", {}).get(field.value)
        if not isinstance(token, str):
            raise AppError(ErrorCode.NOT_FOUND)
        try:
            value = await self._vault.resolve(
                token, org_id=principal.org_id, subject_id=hire_id, field=fields.vault_field(field)
            )
        except TokenNotFoundError:
            raise AppError(ErrorCode.NOT_FOUND) from None
        except CryptoError as error:
            log.warning(
                "reveal refused by the vault: hire=%s field=%s error=%s", hire_id, field.value, type(error).__name__
            )
            await self._audit(principal, "pii.reveal", target, AuditOutcome.FAILED, "vault refused the token")
            raise AppError(ErrorCode.INTERNAL_ERROR, "The value could not be read") from None
        masked = self._masked_or_unmaskable(field, value)
        access_id = await self._access_log.record_reveal(
            user_id=principal.user_id, hire_id=hire_id, field=field, token=token, masked=masked, reason=safe_reason
        )
        await self._audit(principal, "pii.reveal", target, AuditOutcome.SUCCESS, safe_reason)
        log.info("pii revealed: org=%s hire=%s field=%s token=%s", principal.org_id, hire_id, field.value, token)
        return RevealedValue(
            hire_id=hire_id, field=field, value=value, access_id=access_id, expires_in_seconds=REVEAL_TTL_SECONDS
        )

    # --- internals ---------------------------------------------------------------------------------

    async def _org_keys(self, org_id: str) -> None:
        """The org's keys, made on its first sensitive write; a concurrent maker wins harmlessly."""
        try:
            await self._keyring.active(org_id)
        except NoOrgKeysError:
            try:
                await self._keyring.create(org_id)
            except KeyVersionConflictError:
                self._keyring.forget(org_id)

    async def _index(self, org_id: str, field: PiiField, value: str) -> str:
        return await self._keyring.blind_index(org_id, fields.BLIND_INDEXES[field], value)

    async def _write(
        self,
        org_id: str,
        hire_id: str,
        field: PiiField,
        value: str,
        created_by: str,
        consent: AadhaarConsent | None,
    ) -> _Written:
        index_field: BlindIndexField | None = fields.BLIND_INDEXES.get(field)
        index = None
        if index_field is not None:
            index = (index_field.index_name, await self._keyring.blind_index(org_id, index_field, value))
        token = await self._vault.put(
            org_id,
            subject_id=hire_id,
            field=fields.vault_field(field),
            value=value,
            created_by=created_by,
            consent=consent,
        )
        return _Written(field=field, token=token, masked=fields.masked(field, value), index=index)

    async def _purge(self, org_id: str, hire_id: str, written: list[_Written]) -> None:
        """Withdraw vault values of a write that did not complete (best effort: a failure is logged,
        and the original error is the one raised)."""
        for item in written:
            try:
                await self._vault.purge(
                    item.token, org_id=org_id, subject_id=hire_id, field=fields.vault_field(item.field)
                )
            except Exception as error:
                log.error("vault purge failed: hire=%s token=%s error=%s", hire_id, item.token, type(error).__name__)

    async def _audit(
        self, principal: Principal, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> None:
        await self._auditor.audit(
            actor=Actor.user(principal.user_id), action=action, target=target, outcome=outcome, reason=reason
        )

    @staticmethod
    def _consent(consent: Any) -> AadhaarConsent:
        return AadhaarConsent(purpose=redact(consent.purpose.strip()))

    @staticmethod
    def _masked_or_unmaskable(field: PiiField, value: str) -> str:
        try:
            return fields.masked(field, value)
        except ValueError:
            return fields.UNMASKABLE

    def _now(self) -> datetime:
        now = self._clock()
        return now.replace(microsecond=now.microsecond // 1000 * 1000)
