"""The vault record (PRD §7.10 Vault record), its stored document and the bytes its `sig` covers.

    { token, org_id, subject: {type: "new_hire", id}, field, ciphertext, nonce, key_version, version,
      status: active | superseded | purged, created_by, created_at, sig }

The database is not trusted: `record_from_document` checks every field's presence and type and fails
closed with `VaultIntegrityError` (it never echoes values). The signature itself is checked by the
`Vault`, which holds the keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from onb_data import TenantContext

from onb_crypto.envelope import NONCE_BYTES
from onb_crypto.errors import VaultIntegrityError
from onb_crypto.keyring import Sealed, associated_data
from onb_crypto.tokens import is_token

__all__ = [
    "AADHAAR_VAULT_COLLECTION",
    "SIG_BYTES",
    "VAULT_COLLECTION",
    "SubjectType",
    "VaultField",
    "VaultRecord",
    "VaultStatus",
    "collection_for",
    "record_from_document",
    "record_to_document",
    "signing_payload",
]

VAULT_COLLECTION: Final = "pii_vault"
AADHAAR_VAULT_COLLECTION: Final = "pii_vault_aadhaar"
SIG_BYTES: Final = 32  # HMAC-SHA256
_SIG_LABEL: Final = b"onb.pii-vault.sig.v1"
_MAX_ID_CHARS: Final = 128
_DOCUMENT_FIELDS: Final = frozenset(
    {
        "token",
        "org_id",
        "subject",
        "field",
        "ciphertext",
        "nonce",
        "key_version",
        "version",
        "status",
        "created_by",
        "created_at",
        "sig",
    }
)


class VaultField(StrEnum):
    """The sensitive fields (PRD §7.10). Document scans live in GridFS (HIRE-2), not here."""

    MOBILE = "mobile"
    EMAIL = "email"
    AADHAAR = "aadhaar"
    PAN = "pan"
    BANK_ACCOUNT = "bank_account"
    IFSC = "ifsc"
    ACCOUNT_HOLDER_NAME = "account_holder_name"


class VaultStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    PURGED = "purged"


class SubjectType(StrEnum):
    NEW_HIRE = "new_hire"


def collection_for(field: VaultField) -> str:
    """Aadhaar has its own partition (UIDAI Aadhaar Data Vault model); every other field shares one."""
    return AADHAAR_VAULT_COLLECTION if field is VaultField.AADHAAR else VAULT_COLLECTION


def check_id(value: object, what: str) -> str:
    """A non-empty id of at most 128 printable characters without separators."""
    if not isinstance(value, str) or not value or len(value) > _MAX_ID_CHARS or not value.isprintable():
        raise ValueError(f"{what} must be 1-{_MAX_ID_CHARS} printable characters")
    return value


@dataclass(frozen=True, slots=True)
class VaultRecord:
    token: str
    org_id: str
    subject_type: SubjectType
    subject_id: str
    field: VaultField
    ciphertext: bytes
    nonce: bytes
    key_version: int
    version: int
    status: VaultStatus
    created_by: str
    created_at: datetime
    sig: bytes = b""

    def sealed(self) -> Sealed:
        """The ciphertext as the keyring takes it. Not for purged records, which keep none."""
        if self.status is VaultStatus.PURGED:
            raise VaultIntegrityError(f"vault record {self.token} is purged and has no ciphertext")
        return Sealed(self.key_version, self.nonce, self.ciphertext)

    def signed(self, sig: bytes) -> VaultRecord:
        return replace(self, sig=sig)

    def with_status(self, status: VaultStatus) -> VaultRecord:
        """A copy in another status, unsigned. Purging drops the ciphertext and nonce."""
        if status is VaultStatus.PURGED:
            return replace(self, status=status, ciphertext=b"", nonce=b"", sig=b"")
        return replace(self, status=status, sig=b"")


def signing_payload(record: VaultRecord) -> bytes:
    """Every field except `sig`, each length-prefixed, in a fixed order."""
    return associated_data(
        _SIG_LABEL,
        record.token,
        record.org_id,
        record.subject_type.value,
        record.subject_id,
        record.field.value,
        record.ciphertext,
        record.nonce,
        record.key_version,
        record.version,
        record.status.value,
        record.created_by,
        record.created_at.astimezone(UTC).isoformat(timespec="milliseconds"),
    )


def record_to_document(record: VaultRecord) -> dict[str, Any]:
    return {
        "token": record.token,
        "org_id": record.org_id,
        "subject": {"type": record.subject_type.value, "id": record.subject_id},
        "field": record.field.value,
        "ciphertext": record.ciphertext,
        "nonce": record.nonce,
        "key_version": record.key_version,
        "version": record.version,
        "status": record.status.value,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "sig": record.sig,
    }


def _malformed(what: str) -> VaultIntegrityError:
    return VaultIntegrityError(f"vault record is malformed: {what}")


def _positive_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _malformed(what)
    return value


def _enum[E: StrEnum](kind: type[E], value: object, what: str) -> E:
    if not isinstance(value, str):
        raise _malformed(what)
    try:
        return kind(value)
    except ValueError:
        raise _malformed(what) from None


def _subject(value: object) -> tuple[SubjectType, str]:
    if not isinstance(value, Mapping) or set(value) != {"type", "id"}:
        raise _malformed("subject")
    try:
        return _enum(SubjectType, value["type"], "subject.type"), check_id(value["id"], "subject.id")
    except ValueError:
        raise _malformed("subject.id") from None


def _binary(doc: Mapping[str, Any], status: VaultStatus) -> tuple[bytes, bytes, bytes]:
    ciphertext, nonce, sig = doc["ciphertext"], doc["nonce"], doc["sig"]
    if not all(isinstance(v, bytes) for v in (ciphertext, nonce, sig)):
        raise _malformed("ciphertext, nonce and sig must be binary")
    if len(sig) != SIG_BYTES:
        raise _malformed("sig length")
    if status is VaultStatus.PURGED:
        if ciphertext or nonce:
            raise _malformed("a purged record keeps no ciphertext")
    elif len(nonce) != NONCE_BYTES or not ciphertext:
        raise _malformed("nonce or ciphertext")
    return ciphertext, nonce, sig


def _created(doc: Mapping[str, Any]) -> tuple[str, datetime]:
    created_by, created_at = doc["created_by"], doc["created_at"]
    try:
        check_id(created_by, "created_by")
    except ValueError:
        raise _malformed("created_by") from None
    if not isinstance(created_at, datetime):
        raise _malformed("created_at")
    if created_at.tzinfo is None:  # a client without tz_aware returns naive UTC
        created_at = created_at.replace(tzinfo=UTC)
    return created_by, created_at


def record_from_document(doc: Mapping[str, Any]) -> VaultRecord:
    """A record from a stored document, every field checked. Fails closed; the signature is not checked here."""
    if not isinstance(doc, Mapping) or set(doc) - {"_id"} != _DOCUMENT_FIELDS:
        raise _malformed("unexpected or missing fields")
    if not is_token(doc["token"]):
        raise _malformed("token")
    try:
        org_id = TenantContext(doc["org_id"]).org_id
    except (TypeError, ValueError):
        raise _malformed("org_id") from None
    subject_type, subject_id = _subject(doc["subject"])
    status = _enum(VaultStatus, doc["status"], "status")
    ciphertext, nonce, sig = _binary(doc, status)
    created_by, created_at = _created(doc)
    return VaultRecord(
        token=doc["token"],
        org_id=org_id,
        subject_type=subject_type,
        subject_id=subject_id,
        field=_enum(VaultField, doc["field"], "field"),
        ciphertext=ciphertext,
        nonce=nonce,
        key_version=_positive_int(doc["key_version"], "key_version"),
        version=_positive_int(doc["version"], "version"),
        status=status,
        created_by=created_by,
        created_at=created_at,
        sig=sig,
    )
