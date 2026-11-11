"""Document scans of a hire (PRD FR-P3, §7.10 Documents, §10 `POST /hires/{id}/documents`; HIRE-2).

An upload (HR/HR_ADMIN) is checked in this order, nothing stored until every check passes:
allowed media type (422) → the hire is the caller org's (404 otherwise, like a missing one) → an Aadhaar
scan needs the hire's recorded Aadhaar consent (422) → at most `max_bytes` (413) → the bytes carry the
declared type's signature (422). Then the scan is encrypted bound to the hire into the bucket, its
`documents_meta` record is written, and the upload is audited. If the metadata or the audit record
cannot be written, everything written for it is removed (AUD-1: a change that cannot be audited is not
made) and the request fails.

The answer, like `documents_meta`, is metadata only. No route returns the bytes; `read` serves the
service's own callers (tests now, a later audited download).

Logs carry ids, the type and chunk counts. Never bytes, never a file name.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Final

from onb_contracts import ErrorCode, ErrorDetail
from onb_contracts.documents import MAX_DOCUMENT_BYTES, DocumentMeta, DocumentType
from onb_crypto import Keyring
from onb_data import Actor, Auditor, AuditOutcome, DataStore

from onb_api.auth.tokens import Principal
from onb_api.documents.bucket import DocumentBucket, new_document_id
from onb_api.documents.meta import DocumentMetaStore
from onb_api.documents.upload import check_content, media_type_of, read_capped
from onb_api.errors import AppError
from onb_api.hires.org_keys import ensure_org_keys
from onb_api.hires.store import HireStore

__all__ = ["DocumentService"]

log = logging.getLogger(__name__)

_NO_AADHAAR_CONSENT: Final = ErrorDetail(field="query.type", issue="This hire has not consented to storing an Aadhaar")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DocumentService:
    def __init__(
        self,
        *,
        hires: HireStore,
        bucket: DocumentBucket,
        meta: DocumentMetaStore,
        keyring: Keyring,
        auditor: Auditor,
        max_bytes: int = MAX_DOCUMENT_BYTES,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._hires = hires
        self.bucket = bucket
        self.meta = meta
        self._keyring = keyring
        self._auditor = auditor
        self._max_bytes = max_bytes
        self._clock = clock

    @classmethod
    def build(cls, data: DataStore, keyring: Keyring, *, auditor: Auditor) -> DocumentService:
        """The service over one store, with the hire service's keyring (one master key)."""
        return cls(
            hires=HireStore(data),
            bucket=DocumentBucket(data, keyring),
            meta=DocumentMetaStore(data),
            keyring=keyring,
            auditor=auditor,
        )

    async def ensure_indexes(self) -> None:
        await self.bucket.ensure_indexes()
        await self.meta.ensure_indexes()

    async def upload(
        self,
        principal: Principal,
        hire_id: str,
        document_type: DocumentType,
        *,
        content_type: str | None,
        content_length: str | None,
        body: AsyncIterator[bytes],
    ) -> DocumentMeta:
        media_type = media_type_of(content_type)
        hire = await self._hires.get(hire_id)
        if hire is None:
            raise AppError(ErrorCode.NOT_FOUND)
        if document_type is DocumentType.AADHAAR and not _has_aadhaar_consent(hire):
            raise AppError(ErrorCode.VALIDATION_ERROR, details=(_NO_AADHAAR_CONSENT,))
        data = await read_capped(body, declared_length=content_length, limit=self._max_bytes)
        check_content(media_type, data)
        org_id = principal.org_id
        await ensure_org_keys(self._keyring, org_id)
        now = self._now()
        stored = await self.bucket.put(
            org_id,
            hire_id=hire_id,
            document_type=document_type,
            media_type=media_type,
            data=data,
            now=now,
            document_id=new_document_id(),
        )
        try:
            record = await self.meta.add(document_id=stored.id, hire_id=hire_id, document_type=document_type, now=now)
            await self._audit(principal, f"hire:{hire_id}/document/{stored.id}")
        except BaseException:
            await self._undo(stored.id)
            raise
        log.info("document uploaded: org=%s hire=%s document=%s type=%s", org_id, hire_id, stored.id, document_type)
        return record

    async def read(self, org_id: str, *, hire_id: str, document_id: str) -> bytes:
        """The plaintext of one scan, only for the hire it was uploaded for (see `DocumentBucket.read`)."""
        return await self.bucket.read(org_id, hire_id=hire_id, document_id=document_id)

    async def _audit(self, principal: Principal, target: str) -> None:
        await self._auditor.audit(
            actor=Actor.user(principal.user_id), action="document.upload", target=target, outcome=AuditOutcome.SUCCESS
        )

    async def _undo(self, document_id: str) -> None:
        """Take back an upload that cannot be completed (best effort: a failure is logged)."""
        try:
            await self.meta.delete(document_id)
        except Exception as error:
            log.error("undo of document %s metadata failed: %s", document_id, type(error).__name__)
        await self.bucket.delete(document_id)

    def _now(self) -> datetime:
        now = self._clock()
        return now.replace(microsecond=now.microsecond // 1000 * 1000)


def _has_aadhaar_consent(hire: dict[str, Any]) -> bool:
    proof = hire.get("id_proof")
    return isinstance(proof, dict) and proof.get("aadhaar_consent") is True
