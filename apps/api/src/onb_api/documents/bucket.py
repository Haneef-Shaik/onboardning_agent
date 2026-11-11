"""The encrypted document bucket: GridFS layout, written through the tenant repository (PRD §7.10 Documents).

A scan is encrypted whole with AES-256-GCM under the org's key (the Aadhaar key for an Aadhaar scan,
the data key otherwise) before any byte is written, then the ciphertext is split into GridFS chunks:

    documents.files   {_id: "doc_…", org_id, length, chunkSize, uploadDate, filename: <the id>,
                       metadata: {hire_id, type, media_type, alg, key_purpose, key_version, nonce}}
    documents.chunks  {_id, org_id, files_id: "doc_…", n, data}

The AEAD associated data binds `org_id | hire_id | document id | type | media type` (plus org, purpose and
key version, through the keyring), like a vault record, so the bytes open only for the hire they were
uploaded for: re-pointing the files document at another hire, changing its type, or pasting its chunks
into another document makes decryption fail. One tag over the whole ciphertext also catches a missing,
duplicated or altered chunk. Any such failure raises `DecryptionError`; nothing is returned.

The layout is GridFS's (any GridFS reader sees the ciphertext), but every document carries `org_id` and
goes through `TenantRepository` like all other data (PRD §7.9): `DataStore` never hands out the raw
database a driver-level GridFS bucket needs (ADR 0012). No file name or plaintext digest is stored.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, TypeGuard

from onb_contracts.documents import DOCUMENT_ID_PATTERN, DocumentType
from onb_crypto import DecryptionError, KeyPurpose, Keyring, Sealed, UnknownKeyVersionError, associated_data
from onb_data import DataStore, TenantRepository

__all__ = [
    "BUCKET",
    "CHUNKS_COLLECTION",
    "CHUNK_SIZE",
    "FILES_COLLECTION",
    "DocumentBucket",
    "DocumentNotFoundError",
    "StoredDocument",
]

log = logging.getLogger(__name__)

BUCKET: Final = "documents"
FILES_COLLECTION: Final = f"{BUCKET}.files"
CHUNKS_COLLECTION: Final = f"{BUCKET}.chunks"
CHUNK_SIZE: Final = 255 * 1024  # GridFS's default chunk size
ALG: Final = "A256GCM"
_BINDING_LABEL: Final = "onb.document.v1"
_DOCUMENT_ID: Final = re.compile(DOCUMENT_ID_PATTERN)


class DocumentNotFoundError(LookupError):
    """No such document for this hire in this org."""


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: str
    hire_id: str
    type: DocumentType
    uploaded_at: datetime


def new_document_id() -> str:
    return f"doc_{secrets.token_hex(12)}"


def _purpose(document_type: DocumentType) -> KeyPurpose:
    """Aadhaar scans use the org's Aadhaar key, like the Aadhaar number (PRD §7.10 Aadhaar)."""
    return KeyPurpose.AADHAAR if document_type is DocumentType.AADHAAR else KeyPurpose.DATA


def _binding(org_id: str, hire_id: str, document_id: str, document_type: str, media_type: str) -> bytes:
    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type, media_type)


class DocumentBucket:
    def __init__(self, store: DataStore, keyring: Keyring) -> None:
        self._store = store
        self._keyring = keyring

    def _files(self) -> TenantRepository:
        return self._store.tenant(FILES_COLLECTION)

    def _chunks(self) -> TenantRepository:
        return self._store.tenant(CHUNKS_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(
            CHUNKS_COLLECTION, [("org_id", 1), ("files_id", 1), ("n", 1)], unique=True, name="org_files_id_n_unique"
        )
        await self._store.ensure_index(FILES_COLLECTION, [("org_id", 1), ("metadata.hire_id", 1)])

    async def put(
        self,
        org_id: str,
        *,
        hire_id: str,
        document_type: DocumentType,
        media_type: str,
        data: bytes,
        now: datetime,
    ) -> StoredDocument:
        """Encrypt `data` bound to the hire and store it. Chunks first, the files document last, so a
        files document always has its chunks; a failure removes what was written and re-raises."""
        if not data:
            raise ValueError("a document needs at least one byte")
        doc_id = new_document_id()
        purpose = _purpose(document_type)
        bound = _binding(org_id, hire_id, doc_id, document_type.value, media_type)
        sealed = await self._keyring.encrypt(org_id, data, associated_data=bound, purpose=purpose)
        ciphertext = sealed.ciphertext
        chunks = [
            {"files_id": doc_id, "n": n, "data": ciphertext[offset : offset + CHUNK_SIZE]}
            for n, offset in enumerate(range(0, len(ciphertext), CHUNK_SIZE))
        ]
        files = {
            "_id": doc_id,
            "length": len(ciphertext),
            "chunkSize": CHUNK_SIZE,
            "uploadDate": now,
            "filename": doc_id,
            "metadata": {
                "hire_id": hire_id,
                "type": document_type.value,
                "media_type": media_type,
                "alg": ALG,
                "key_purpose": purpose.value,
                "key_version": sealed.key_version,
                "nonce": sealed.nonce,
            },
        }
        try:
            await self._chunks().insert_many(chunks)
            await self._files().insert_one(files)
        except BaseException:
            await self._remove(doc_id)
            raise
        log.info("document stored: org=%s hire=%s document=%s chunks=%d", org_id, hire_id, doc_id, len(chunks))
        return StoredDocument(id=doc_id, hire_id=hire_id, type=document_type, uploaded_at=now)

    async def read(self, org_id: str, *, hire_id: str, document_id: str) -> bytes:
        """The plaintext, only for the hire it was uploaded for. `DocumentNotFoundError` when this org has
        no such document for this hire; `DecryptionError` when anything stored was changed."""
        files = await self._files().find_one({"_id": _checked_id(document_id), "metadata.hire_id": hire_id})
        if files is None:
            raise DocumentNotFoundError(f"no document {document_id} for hire {hire_id}")
        sealed, purpose, document_type, media_type = _sealed_header(files)
        chunks = await self._chunks().find({"files_id": document_id}, sort=[("n", 1)])
        ciphertext = _joined(chunks, files)
        bound = _binding(org_id, hire_id, document_id, document_type, media_type)
        try:
            return await self._keyring.decrypt(
                org_id,
                Sealed(sealed.key_version, sealed.nonce, ciphertext),
                associated_data=bound,
                purpose=purpose,
            )
        except UnknownKeyVersionError:
            raise _refused(document_id, "unknown key version") from None

    async def delete(self, document_id: str) -> None:
        await self._remove(_checked_id(document_id))

    async def _remove(self, document_id: str) -> None:
        """Delete every chunk, then the files document; each step on its own, so one failure does not
        leave the rest behind (best effort: failures are logged)."""
        try:
            chunks = await self._chunks().find({"files_id": document_id}, projection={"_id": 1})
        except Exception as error:
            log.error("document %s chunks not listed for removal: %s", document_id, type(error).__name__)
            chunks = []
        for chunk in chunks:
            try:
                await self._chunks().delete_one({"_id": chunk["_id"]})
            except Exception as error:
                log.error("document %s chunk not removed: %s", document_id, type(error).__name__)
        try:
            await self._files().delete_one({"_id": document_id})
        except Exception as error:
            log.error("document %s not fully removed: %s", document_id, type(error).__name__)


def _checked_id(document_id: object) -> str:
    """A server-made document id; anything else (an operator, another shape) finds nothing."""
    if not isinstance(document_id, str) or not _DOCUMENT_ID.fullmatch(document_id):
        raise DocumentNotFoundError("not a document id")
    return document_id


def _refused(document_id: object, why: str) -> DecryptionError:
    log.warning("document refused: document=%s reason=%s", document_id, why)
    return DecryptionError(f"document {document_id} cannot be opened: {why}")


def _sealed_header(files: dict[str, Any]) -> tuple[Sealed, KeyPurpose, str, str]:
    """The stored encryption header, checked; any malformed field fails closed."""
    meta = files.get("metadata")
    if not isinstance(meta, dict) or meta.get("alg") != ALG:
        raise _refused(files.get("_id"), "malformed metadata")
    try:
        purpose = KeyPurpose(str(meta.get("key_purpose")))
        # The stored values go to `Sealed` as they are: it refuses a non-int version and a nonce that is not
        # 12 bytes (never `bytes(<int>)`, which would allocate instead of refusing).
        sealed = Sealed(meta.get("key_version"), meta.get("nonce"), b"")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _refused(files.get("_id"), "malformed encryption header") from None
    document_type, media_type = meta.get("type"), meta.get("media_type")
    if not purpose.encrypts or not isinstance(document_type, str) or not isinstance(media_type, str):
        raise _refused(files.get("_id"), "malformed metadata")
    return sealed, purpose, document_type, media_type


def _joined(chunks: list[dict[str, Any]], files: dict[str, Any]) -> bytes:
    """The ciphertext from contiguous chunks 0..k-1 whose total is the recorded length."""
    length, chunk_size = files.get("length"), files.get("chunkSize")
    if not _positive_int(length) or not _positive_int(chunk_size):
        raise _refused(files.get("_id"), "malformed length")
    expected = -(-length // chunk_size)  # ceiling, in integers
    if len(chunks) != expected or [c.get("n") for c in chunks] != list(range(expected)):
        raise _refused(files.get("_id"), "missing or duplicated chunks")
    data = b"".join(bytes(c.get("data") or b"") for c in chunks)
    if len(data) != length:
        raise _refused(files.get("_id"), "length mismatch")
    return data


def _positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
