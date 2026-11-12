"""The PII vault (PRD §5.5, §7.10, FR-P4): the one place raw sensitive values live.

`put` encrypts a value with AES-256-GCM under the org's data key (the Aadhaar key for Aadhaar), binds
`org_id | subject.id | field | token | version` as associated data, signs the whole record with the
org's HMAC signing key and returns a fresh `ptk_` token. Updating a field writes a new version under a
new token and marks the previous record `superseded`. Every read finds the record by token, checks
its shape, partition and signature, then the reader's expected subject and field, and only then
decrypts. Any failure raises; no value is released (fails closed).

Only the API layer holds a `Vault` (HR/HR_ADMIN writes, reveals). Agents, MCP tools and event
consumers never get one. Every call runs inside `tenant_scope(org_id)` for the same org.

Logs carry tokens, fields, versions and reasons. Never values.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from onb_data import TenantContext, TenantViolationError, UpdateOutcome, current_tenant
from pymongo.errors import DuplicateKeyError

from onb_crypto import normalize
from onb_crypto.errors import (
    DecryptionError,
    SupersededTokenError,
    TokenBindingError,
    TokenNotFoundError,
    UnknownKeyVersionError,
    VaultConflictError,
    VaultIntegrityError,
)
from onb_crypto.keyring import Keyring, associated_data
from onb_crypto.keys import KeyPurpose
from onb_crypto.tokens import is_token, new_token
from onb_crypto.vault_record import (
    SubjectType,
    VaultField,
    VaultRecord,
    VaultStatus,
    check_id,
    collection_for,
    record_from_document,
    record_to_document,
    signing_payload,
)

__all__ = [
    "CONSENT_COLLECTION",
    "PURGED",
    "AadhaarConsent",
    "PurgeOutcome",
    "Vault",
    "VaultRepository",
    "VaultStore",
]

log = logging.getLogger(__name__)

CONSENT_COLLECTION: Final = "pii_consent"
PURGED: Final = "[purged]"
_MAX_VALUE_CHARS: Final = 1000
_MAX_PURPOSE_CHARS: Final = 500


class VaultRepository(Protocol):
    """The part of T-1's `TenantRepository` the vault uses."""

    async def find(
        self, query: Mapping[str, Any] | None = None, *, sort: Sequence[tuple[str, int]] | None = None, limit: int = 0
    ) -> list[dict[str, Any]]: ...

    async def insert_one(self, document: Mapping[str, Any]) -> Any: ...

    async def update_one(self, query: Mapping[str, Any] | None, update: Mapping[str, Any]) -> UpdateOutcome: ...

    async def delete_one(self, query: Mapping[str, Any] | None) -> int: ...


class VaultStore(Protocol):
    """T-1's `DataStore`: tenant repositories only."""

    def tenant(self, collection: str) -> VaultRepository: ...


@dataclass(frozen=True, slots=True)
class AadhaarConsent:
    """The hire's consent to store their Aadhaar, captured with it (PRD §7.10 Aadhaar, Retention)."""

    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip() or len(self.purpose) > _MAX_PURPOSE_CHARS:
            raise ValueError(f"a consent needs a stated purpose of at most {_MAX_PURPOSE_CHARS} characters")


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """One vault record `purge_subject` found: its token and field, and whether this call purged it."""

    token: str
    field: VaultField
    version: int
    newly_purged: bool


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _check_org(org_id: object) -> str:
    try:
        valid = TenantContext(org_id).org_id  # type: ignore[arg-type]  # validated right here
    except ValueError:
        raise ValueError("invalid org id: 1-128 letters, digits, '_' or '-'") from None
    if current_tenant().org_id != valid:
        raise TenantViolationError("vault called for an org other than the tenant context")
    return valid


def _check_field(field: object) -> VaultField:
    if not isinstance(field, VaultField):
        raise ValueError("field must be a VaultField")
    return field


def _purpose_for(field: VaultField) -> KeyPurpose:
    return KeyPurpose.AADHAAR if field is VaultField.AADHAAR else KeyPurpose.DATA


def _plaintext(field: VaultField, value: object) -> bytes:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_VALUE_CHARS:
        raise ValueError(f"a {field.value} value must be 1-{_MAX_VALUE_CHARS} characters")
    if field is VaultField.AADHAAR:
        value = normalize.aadhaar(value)  # raises without echoing the value
    return value.encode("utf-8")


def _check_consent(field: VaultField, consent: AadhaarConsent | None) -> None:
    if field is VaultField.AADHAAR and not isinstance(consent, AadhaarConsent):
        raise ValueError("an Aadhaar value needs the hire's consent record")
    if field is not VaultField.AADHAAR and consent is not None:
        raise ValueError("a consent record is only taken with an Aadhaar value")


def _millis(moment: datetime) -> datetime:
    """MongoDB keeps milliseconds; the signed time must survive a round trip."""
    moment = moment.astimezone(UTC)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


class Vault:
    def __init__(self, keyring: Keyring, store: VaultStore, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._keyring = keyring
        self._store = store
        self._clock = clock

    @property
    def keyring(self) -> Keyring:
        return self._keyring

    @staticmethod
    def associated_data(org_id: str, subject_id: str, field: VaultField, token: str, version: int) -> bytes:
        """The AEAD binding (PRD §7.10 Encryption): `org_id | subject.id | field | token | version`."""
        return associated_data(org_id, subject_id, field.value, token, version)

    # --- writes ------------------------------------------------------------------------------------

    async def put(
        self,
        org_id: str,
        *,
        subject_id: str,
        field: VaultField,
        value: str,
        created_by: str,
        consent: AadhaarConsent | None = None,
    ) -> str:
        """Store a new value for the subject's field and return its new token. A previous active value
        becomes `superseded` (FR-P4). Aadhaar goes to its own partition with a consent record."""
        org_id = _check_org(org_id)
        field = _check_field(field)
        check_id(subject_id, "subject_id")
        check_id(created_by, "created_by")
        _check_consent(field, consent)
        plaintext = _plaintext(field, value)
        repo = self._store.tenant(collection_for(field))
        current = await self._current(repo, org_id, subject_id, field)
        version = 1 if current is None else current.version + 1
        now = _millis(self._clock())
        token = new_token(now=now)
        ad = self.associated_data(org_id, subject_id, field, token, version)
        sealed = await self._keyring.encrypt(org_id, plaintext, associated_data=ad, purpose=_purpose_for(field))
        record = VaultRecord(
            token=token,
            org_id=org_id,
            subject_type=SubjectType.NEW_HIRE,
            subject_id=subject_id,
            field=field,
            ciphertext=sealed.ciphertext,
            nonce=sealed.nonce,
            key_version=sealed.key_version,
            version=version,
            status=VaultStatus.ACTIVE,
            created_by=created_by,
            created_at=now,
        )
        signed = await self._sign(record)
        await self._insert(repo, signed, consent)
        await self._supersede_older(repo, signed)
        log.info("vault put: org=%s token=%s field=%s version=%d", org_id, token, field.value, version)
        return token

    async def purge(self, token: str, *, org_id: str, subject_id: str, field: VaultField) -> None:
        """Delete the ciphertext and keep the record as `purged` (PRD §7.10 Retention). Idempotent."""
        record = await self.verify(token, org_id=org_id, subject_id=subject_id, field=field)
        if record.status is VaultStatus.PURGED:
            return
        purged = await self._sign(record.with_status(VaultStatus.PURGED))
        outcome = await self._store.tenant(collection_for(record.field)).update_one(
            {"token": record.token, "sig": record.sig},
            {"$set": {"status": purged.status.value, "ciphertext": b"", "nonce": b"", "sig": purged.sig}},
        )
        if outcome.matched != 1:
            raise VaultConflictError(f"vault record {token} changed while it was being purged; retry")
        log.info("vault purge: org=%s token=%s field=%s", record.org_id, token, record.field.value)

    async def purge_subject(self, org_id: str, *, subject_id: str) -> tuple[PurgeOutcome, ...]:
        """Purge every record of the subject, in both partitions, superseded versions included (PRD §7.10
        Retention). Each record is verified before it is purged, so a tampered one raises and is left as
        it is. Already purged records are reported with `newly_purged=False`. The consent record is not
        touched. Idempotent."""
        org_id = _check_org(org_id)
        check_id(subject_id, "subject_id")
        outcomes: list[PurgeOutcome] = []
        for collection in sorted({collection_for(field) for field in VaultField}):
            docs = await self._store.tenant(collection).find(
                {"subject.type": SubjectType.NEW_HIRE.value, "subject.id": subject_id},
                sort=[("field", 1), ("version", 1)],
            )
            for doc in docs:
                outcomes.append(await self._purge_document(org_id, subject_id, collection, doc))
        return tuple(outcomes)

    async def _purge_document(
        self, org_id: str, subject_id: str, collection: str, doc: Mapping[str, Any]
    ) -> PurgeOutcome:
        token = doc.get("token")
        try:
            field = VaultField(str(doc.get("field")))
        except ValueError:
            raise self._refuse(org_id, str(token), "the record names no known field") from None
        if collection_for(field) != collection:
            raise self._refuse(org_id, str(token), f"a {field.value} record is in the {collection} partition")
        record = await self.verify(str(token), org_id=org_id, subject_id=subject_id, field=field)
        was_purged = record.status is VaultStatus.PURGED
        await self.purge(record.token, org_id=org_id, subject_id=subject_id, field=field)
        return PurgeOutcome(token=record.token, field=field, version=record.version, newly_purged=not was_purged)

    # --- reads -------------------------------------------------------------------------------------

    async def verify(self, token: str, *, org_id: str, subject_id: str, field: VaultField) -> VaultRecord:
        """The record behind `token`, signature verified and bound to the expected subject and field.
        No decryption. Raises a `VaultError` on any mismatch."""
        org_id = _check_org(org_id)
        field = _check_field(field)
        record = await self._read(field, token, org_id)
        if record.subject_id != subject_id or record.field is not field:
            log.warning("vault binding refused: org=%s token=%s field=%s", org_id, token, field.value)
            raise TokenBindingError(f"token {token} is not bound to that subject and field")
        return record

    async def resolve(
        self, token: str, *, org_id: str, subject_id: str, field: VaultField, allow_superseded: bool = False
    ) -> str:
        """The value behind `token`, after `verify`. `[purged]` for a purged record; a superseded record
        only with `allow_superseded`."""
        record = await self.verify(token, org_id=org_id, subject_id=subject_id, field=field)
        if record.status is VaultStatus.PURGED:
            return PURGED
        if not allow_superseded and not await self._is_live(record):
            raise SupersededTokenError(f"token {token} was superseded by a newer {record.field.value} value")
        ad = self.associated_data(record.org_id, record.subject_id, record.field, record.token, record.version)
        try:
            plaintext = await self._keyring.decrypt(
                record.org_id, record.sealed(), associated_data=ad, purpose=_purpose_for(record.field)
            )
        except DecryptionError:
            raise self._refuse(record.org_id, token, "ciphertext does not open under its binding") from None
        return plaintext.decode("utf-8")

    # --- internals ---------------------------------------------------------------------------------

    async def _read(self, field: VaultField, token: str, org_id: str) -> VaultRecord:
        if not is_token(token):
            raise TokenNotFoundError("not a vault token")
        collection = collection_for(field)
        docs = await self._store.tenant(collection).find({"token": token}, limit=2)
        if not docs:
            raise TokenNotFoundError(f"no vault record for token {token}")
        if len(docs) > 1:
            raise self._refuse(org_id, token, "the token appears twice")
        try:
            record = record_from_document(docs[0])
        except VaultIntegrityError as error:
            raise self._refuse(org_id, token, str(error)) from None
        if record.token != token or record.org_id != org_id:
            raise self._refuse(org_id, token, "token or org differs from the lookup")
        if collection_for(record.field) != collection:
            raise self._refuse(org_id, token, f"a {record.field.value} record is in the {collection} partition")
        if not hmac.compare_digest(record.sig, await self._signature(record)):
            raise self._refuse(org_id, token, "signature does not verify")
        return record

    async def _current(
        self, repo: VaultRepository, org_id: str, subject_id: str, field: VaultField
    ) -> VaultRecord | None:
        """The newest record of the subject's field, verified (an update never builds on a tampered one)."""
        docs = await repo.find(
            {"subject.type": SubjectType.NEW_HIRE.value, "subject.id": subject_id, "field": field.value},
            sort=[("version", -1)],
            limit=1,
        )
        if not docs:
            return None
        token = docs[0].get("token")
        if not is_token(token):
            raise self._refuse(org_id, "(none)", f"the newest {field.value} record of {subject_id} has no valid token")
        return await self._read(field, token, org_id)

    async def _is_live(self, record: VaultRecord) -> bool:
        """Active and the newest version of its subject's field. An older record that still says `active`
        (a half-finished update, or an old signed copy written back) is treated as superseded."""
        if record.status is not VaultStatus.ACTIVE:
            return False
        repo = self._store.tenant(collection_for(record.field))
        newest = await self._current(repo, record.org_id, record.subject_id, record.field)
        if newest is None or newest.token != record.token:
            log.warning("vault stale active record: org=%s token=%s", record.org_id, record.token)
            return False
        return True

    async def _insert(self, repo: VaultRepository, record: VaultRecord, consent: AadhaarConsent | None) -> None:
        """The new record, with its consent record first; the consent is removed if the record is not stored."""
        if consent is not None:
            await self._record_consent(record, consent)
        try:
            await repo.insert_one(record_to_document(record))
        except Exception as error:
            if consent is not None:
                await self._store.tenant(CONSENT_COLLECTION).delete_one({"token": record.token})
            if isinstance(error, DuplicateKeyError):
                raise VaultConflictError(
                    f"{record.field.value} of {record.subject_id} was written concurrently; retry"
                ) from None
            raise

    async def _supersede_older(self, repo: VaultRepository, newest: VaultRecord) -> None:
        """Mark every older active record of the subject's field `superseded` (also repairing what an earlier
        half-finished update left). A record that stopped being active meanwhile needs nothing more."""
        docs = await repo.find(
            {
                "subject.type": newest.subject_type.value,
                "subject.id": newest.subject_id,
                "field": newest.field.value,
                "status": VaultStatus.ACTIVE.value,
            }
        )
        for doc in docs:
            if doc.get("token") == newest.token:
                continue
            token = doc.get("token")
            if not is_token(token):
                raise self._refuse(newest.org_id, "(none)", f"an active {newest.field.value} record has no valid token")
            old = await self._read(newest.field, token, newest.org_id)
            if old.status is VaultStatus.ACTIVE and old.version < newest.version:
                await self._supersede(repo, old)

    async def _supersede(self, repo: VaultRepository, current: VaultRecord) -> None:
        superseded = await self._sign(current.with_status(VaultStatus.SUPERSEDED))
        outcome = await repo.update_one(
            {"token": current.token, "sig": current.sig, "status": VaultStatus.ACTIVE.value},
            {"$set": {"status": superseded.status.value, "sig": superseded.sig}},
        )
        if outcome.matched == 1:
            return
        now = await self._read(current.field, current.token, current.org_id)
        if now.status is VaultStatus.ACTIVE:
            raise VaultConflictError(f"vault record {current.token} changed before it could be superseded; retry")

    async def _record_consent(self, record: VaultRecord, consent: AadhaarConsent) -> None:
        await self._store.tenant(CONSENT_COLLECTION).insert_one(
            {
                "subject": {"type": record.subject_type.value, "id": record.subject_id},
                "field": record.field.value,
                "token": record.token,
                "purpose": consent.purpose,
                "captured_by": record.created_by,
                "captured_at": record.created_at,
            }
        )

    async def _signature(self, record: VaultRecord) -> bytes:
        try:
            keys = await self._keyring.key_version(record.org_id, record.key_version)
        except UnknownKeyVersionError:
            raise self._refuse(record.org_id, record.token, "unknown key version") from None
        key = keys.key(KeyPurpose.SIGNING).reveal()
        return hmac.new(key, signing_payload(record), hashlib.sha256).digest()

    async def _sign(self, record: VaultRecord) -> VaultRecord:
        return record.signed(await self._signature(record))

    @staticmethod
    def _refuse(org_id: str, token: str, reason: str) -> VaultIntegrityError:
        log.warning("vault record refused: org=%s token=%s reason=%s", org_id, token, reason)
        return VaultIntegrityError(f"vault record {token} fails closed: {reason}")
