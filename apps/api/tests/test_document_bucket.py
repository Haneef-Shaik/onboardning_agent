"""The encrypted document bucket and `documents_meta` (HIRE-2 boxes 1 and 3; PRD FR-P3, §7.10 Documents).

The bucket runs over the in-memory database under the real `DataStore`, tenant scoping and keyring, so
what is asserted about the stored bytes is what production stores. The scan bytes are synthetic.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_api.documents.bucket import (
    CHUNK_SIZE,
    CHUNKS_COLLECTION,
    FILES_COLLECTION,
    DocumentBucket,
    StoredDocument,
)
from onb_api.documents.meta import META_COLLECTION, DocumentMetaStore
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store
from onb_contracts.documents import DocumentType
from onb_crypto import DecryptionError, Keyring
from onb_data import DataStore, tenant_scope

ORG = "org_acme"
OTHER_ORG = "org_globex"
HIRE = "h_acme_001"
OTHER_HIRE = "h_acme_002"
NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
# A synthetic "scan": a PNG signature, a recognisable marker, and random bytes over three chunks.
MARKER = b"SYNTHETIC-AADHAAR-SCAN-9999-0000-4202"
PLAINTEXT = b"\x89PNG\r\n\x1a\n" + MARKER + secrets.token_bytes(2 * CHUNK_SIZE + 1234)


def _run[T](org: str, call: Awaitable[T]) -> T:
    async def scoped() -> T:
        with tenant_scope(org):
            return await call

    return asyncio.run(scoped())


@pytest.fixture
def db() -> MemoryDatabase:
    return MemoryDatabase()


@pytest.fixture
def store(db: MemoryDatabase) -> DataStore:
    return memory_store(db)


@pytest.fixture
def keyring(store: DataStore) -> Keyring:
    ring = memory_keyring(store)
    for org in (ORG, OTHER_ORG):
        _run(org, ring.create(org))
    return ring


@pytest.fixture
def bucket(store: DataStore, keyring: Keyring) -> DocumentBucket:
    return DocumentBucket(store, keyring)


def _put(bucket: DocumentBucket, *, org: str = ORG, hire: str = HIRE, data: bytes = PLAINTEXT) -> StoredDocument:
    return _run(
        org,
        bucket.put(org, hire_id=hire, document_type=DocumentType.AADHAAR, media_type="image/png", data=data, now=NOW),
    )


def _stored_bytes(db: MemoryDatabase, document_id: str) -> bytes:
    chunks = sorted((c for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == document_id), key=lambda c: c["n"])
    return b"".join(bytes(c["data"]) for c in chunks)


# --- box 1: GridFS bytes differ from the plaintext, decrypt only with the matching hire binding --------


def test_the_gridfs_bytes_differ_from_the_plaintext(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    stored = _put(bucket)
    raw = _stored_bytes(db, stored.id)
    assert raw
    assert raw != PLAINTEXT
    assert MARKER not in raw
    assert PLAINTEXT[:64] not in raw
    # Nothing outside the chunks carries the plaintext either.
    assert MARKER not in repr(list(db.all_documents())).encode()
    # GridFS layout: a files document and contiguous chunks of at most CHUNK_SIZE.
    files = [f for f in db.raw(FILES_COLLECTION) if f["_id"] == stored.id]
    assert len(files) == 1
    assert files[0]["length"] == len(raw)
    assert files[0]["chunkSize"] == CHUNK_SIZE
    chunks = [c for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == stored.id]
    assert [c["n"] for c in sorted(chunks, key=lambda c: c["n"])] == list(range(len(chunks)))
    assert len(raw) == len(PLAINTEXT) + 16  # AES-GCM: the plaintext's length plus a 16-byte tag
    assert len(chunks) == -(-len(raw) // CHUNK_SIZE) == 3
    assert {c["org_id"] for c in chunks} | {files[0]["org_id"]} == {ORG}


def test_the_bytes_decrypt_with_the_matching_hire_binding(bucket: DocumentBucket) -> None:
    stored = _put(bucket)
    assert _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id)) == PLAINTEXT


def test_another_hire_cannot_decrypt_them(bucket: DocumentBucket) -> None:
    stored = _put(bucket)
    with pytest.raises(LookupError):  # not this hire's document
        _run(ORG, bucket.read(ORG, hire_id=OTHER_HIRE, document_id=stored.id))


def test_rebinding_the_file_to_another_hire_in_the_database_fails_to_decrypt(
    bucket: DocumentBucket, db: MemoryDatabase
) -> None:
    """Re-pointing the files document at another hire (a direct DB edit) must not open the bytes."""
    stored = _put(bucket)
    _rewrite(db, FILES_COLLECTION, stored.id, lambda f: {**f, "metadata": {**f["metadata"], "hire_id": OTHER_HIRE}})
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=OTHER_HIRE, document_id=stored.id))


@pytest.mark.parametrize("field", ["type", "media_type"])
def test_changing_the_bound_metadata_fails_to_decrypt(bucket: DocumentBucket, db: MemoryDatabase, field: str) -> None:
    stored = _put(bucket)
    swapped = {"type": "pan", "media_type": "application/pdf"}[field]
    _rewrite(db, FILES_COLLECTION, stored.id, lambda f: {**f, "metadata": {**f["metadata"], field: swapped}})
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


def test_ciphertext_copied_into_another_hires_document_fails_to_decrypt(
    bucket: DocumentBucket, db: MemoryDatabase
) -> None:
    """Chunks of hire 1's scan pasted over hire 2's: the binding (hire, document id) refuses them."""
    first = _put(bucket, hire=HIRE)
    second = _put(bucket, hire=OTHER_HIRE)
    source = {c["n"]: c["data"] for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == first.id}
    first_files = next(f for f in db.raw(FILES_COLLECTION) if f["_id"] == first.id)
    for n, data in source.items():
        _rewrite_chunk(db, second.id, n, data)
    _rewrite(
        db,
        FILES_COLLECTION,
        second.id,
        lambda f: {**f, "metadata": {**f["metadata"], "nonce": first_files["metadata"]["nonce"]}},
    )
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=OTHER_HIRE, document_id=second.id))


def test_a_flipped_byte_fails_to_decrypt(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    stored = _put(bucket)
    chunk = next(c for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == stored.id and c["n"] == 1)
    data = bytearray(chunk["data"])
    data[7] ^= 0x01
    _rewrite_chunk(db, stored.id, 1, bytes(data))
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


@pytest.mark.parametrize("damage", ["drop_last", "drop_middle", "duplicate_n"])
def test_missing_or_reordered_chunks_are_refused(bucket: DocumentBucket, db: MemoryDatabase, damage: str) -> None:
    stored = _put(bucket)
    collection = db[CHUNKS_COLLECTION]
    mine = [c for c in collection.documents if c["files_id"] == stored.id]
    if damage == "drop_last":
        gone = max(mine, key=lambda c: c["n"])
        collection.documents = [c for c in collection.documents if c is not gone]
    elif damage == "drop_middle":
        collection.documents = [c for c in collection.documents if not (c["files_id"] == stored.id and c["n"] == 1)]
    else:
        collection.documents = [
            {**c, "n": 0} if c["files_id"] == stored.id and c["n"] == 1 else c for c in collection.documents
        ]
    with pytest.raises(DecryptionError, match="missing or duplicated chunks"):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


def test_ciphertext_copied_into_another_document_of_the_same_hire_fails_to_decrypt(
    bucket: DocumentBucket, db: MemoryDatabase
) -> None:
    """The document id is bound too: one scan pasted over another scan of the same hire and type."""
    first = _put(bucket)
    second = _put(bucket)
    first_files = next(f for f in db.raw(FILES_COLLECTION) if f["_id"] == first.id)
    for chunk in (c for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == first.id):
        _rewrite_chunk(db, second.id, chunk["n"], chunk["data"])
    _rewrite(db, FILES_COLLECTION, second.id, lambda f: {**f, "metadata": dict(first_files["metadata"])})
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=second.id))


def test_another_org_cannot_read_the_document(bucket: DocumentBucket) -> None:
    stored = _put(bucket)
    with pytest.raises(LookupError):
        _run(OTHER_ORG, bucket.read(OTHER_ORG, hire_id=HIRE, document_id=stored.id))


def test_an_aadhaar_scan_uses_the_aadhaar_key_and_a_pan_scan_the_data_key(
    bucket: DocumentBucket, db: MemoryDatabase
) -> None:
    aadhaar = _put(bucket)
    pan = _run(
        ORG,
        bucket.put(ORG, hire_id=HIRE, document_type=DocumentType.PAN, media_type="image/png", data=PLAINTEXT, now=NOW),
    )
    purposes = {f["_id"]: f["metadata"]["key_purpose"] for f in db.raw(FILES_COLLECTION)}
    assert purposes == {aadhaar.id: "aadhaar", pan.id: "data"}
    assert _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=pan.id)) == PLAINTEXT


def test_a_purpose_swap_in_the_database_fails_to_decrypt(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    stored = _put(bucket)
    _rewrite(db, FILES_COLLECTION, stored.id, lambda f: {**f, "metadata": {**f["metadata"], "key_purpose": "data"}})
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


def test_the_files_document_keeps_no_file_name_or_plaintext_digest(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    stored = _put(bucket)
    files = next(f for f in db.raw(FILES_COLLECTION) if f["_id"] == stored.id)
    assert files["filename"] == stored.id
    assert set(files) == {"_id", "org_id", "length", "chunkSize", "uploadDate", "filename", "metadata"}
    assert set(files["metadata"]) == {"hire_id", "type", "media_type", "alg", "key_purpose", "key_version", "nonce"}


def test_a_failed_chunk_write_leaves_nothing_behind(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    db[FILES_COLLECTION].fail_next_insert = RuntimeError("database went away")
    with pytest.raises(RuntimeError):
        _put(bucket)
    assert db.raw(CHUNKS_COLLECTION) == []
    assert db.raw(FILES_COLLECTION) == []


def test_delete_removes_the_files_document_and_every_chunk(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    keep = _put(bucket, hire=OTHER_HIRE)
    stored = _put(bucket)
    _run(ORG, bucket.delete(stored.id))
    assert {c["files_id"] for c in db.raw(CHUNKS_COLLECTION)} == {keep.id}
    assert [f["_id"] for f in db.raw(FILES_COLLECTION)] == [keep.id]


def test_an_empty_scan_is_refused(bucket: DocumentBucket) -> None:
    with pytest.raises(ValueError, match="at least one byte"):
        _put(bucket, data=b"")


# --- box 3: documents_meta holds only ids, type, uploaded_at and verified -------------------------------


def test_documents_meta_holds_only_ids_type_uploaded_at_and_verified(store: DataStore, db: MemoryDatabase) -> None:
    """Box 3, the schema test: the stored document has exactly these keys and value types."""
    meta = DocumentMetaStore(store)
    record = _run(ORG, meta.add(document_id="doc_" + "a" * 24, hire_id=HIRE, document_type=DocumentType.PAN, now=NOW))
    (stored,) = db.raw(META_COLLECTION)
    assert set(stored) == {"_id", "org_id", "hire_id", "type", "uploaded_at", "verified"}
    assert stored == {
        "_id": "doc_" + "a" * 24,
        "org_id": ORG,
        "hire_id": HIRE,
        "type": "pan",
        "uploaded_at": NOW,
        "verified": False,
    }
    assert record.model_dump() == {
        "id": "doc_" + "a" * 24,
        "hire_id": HIRE,
        "type": DocumentType.PAN,
        "uploaded_at": NOW,
        "verified": False,
    }


def test_documents_meta_is_scoped_to_the_org(store: DataStore, db: MemoryDatabase) -> None:
    meta = DocumentMetaStore(store)
    _run(ORG, meta.add(document_id="doc_" + "b" * 24, hire_id=HIRE, document_type=DocumentType.PAN, now=NOW))
    _run(OTHER_ORG, meta.delete("doc_" + "b" * 24))  # another org's delete reaches nothing
    assert [m["_id"] for m in db.raw(META_COLLECTION)] == ["doc_" + "b" * 24]
    _run(ORG, meta.delete("doc_" + "b" * 24))
    assert db.raw(META_COLLECTION) == []


# --- a malformed stored header or length fails closed, before any decryption ---------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"alg": "A128CBC"},
        {"nonce": b"short"},
        {"nonce": None},
        {"key_purpose": "signing"},
        {"key_purpose": "nonsense"},
        {"key_version": "1"},
        {"key_version": 0},
        {"type": None},
        {"media_type": 7},
    ],
    ids=[
        "alg",
        "nonce-length",
        "nonce-missing",
        "purpose-not-encrypting",
        "purpose-unknown",
        "version-str",
        "version-zero",
        "type-missing",
        "media-type-not-str",
    ],
)
def test_a_malformed_encryption_header_fails_closed(
    bucket: DocumentBucket, db: MemoryDatabase, change: dict[str, Any]
) -> None:
    stored = _put(bucket)
    _rewrite(db, FILES_COLLECTION, stored.id, lambda f: {**f, "metadata": {**f["metadata"], **change}})
    with pytest.raises(DecryptionError):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


@pytest.mark.parametrize("change", [{"length": 0}, {"length": "12"}, {"chunkSize": 0}, {"chunkSize": None}])
def test_a_malformed_length_fails_closed(bucket: DocumentBucket, db: MemoryDatabase, change: dict[str, Any]) -> None:
    stored = _put(bucket)
    _rewrite(db, FILES_COLLECTION, stored.id, lambda f: {**f, **change})
    with pytest.raises(DecryptionError, match="malformed length"):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


def test_a_truncated_chunk_fails_closed_on_the_length(bucket: DocumentBucket, db: MemoryDatabase) -> None:
    stored = _put(bucket)
    chunk = next(c for c in db.raw(CHUNKS_COLLECTION) if c["files_id"] == stored.id and c["n"] == 0)
    _rewrite_chunk(db, stored.id, 0, bytes(chunk["data"])[:-1])
    with pytest.raises(DecryptionError, match="length mismatch"):
        _run(ORG, bucket.read(ORG, hire_id=HIRE, document_id=stored.id))


def test_a_failed_removal_is_logged_not_raised(
    bucket: DocumentBucket, db: MemoryDatabase, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _put(bucket)

    async def broken(*_: Any, **__: Any) -> int:
        raise RuntimeError("database went away")

    monkeypatch.setattr(db[FILES_COLLECTION], "delete_one", broken)
    _run(ORG, bucket.delete(stored.id))
    assert f"document {stored.id} not fully removed: RuntimeError" in caplog.text


# --- helpers ----------------------------------------------------------------------------------------------


def _rewrite(db: MemoryDatabase, collection: str, doc_id: str, change: Any) -> None:
    coll = db[collection]
    coll.documents = [change(dict(d)) if d["_id"] == doc_id else d for d in coll.documents]


def _rewrite_chunk(db: MemoryDatabase, files_id: str, n: int, data: bytes) -> None:
    coll = db[CHUNKS_COLLECTION]
    coll.documents = [{**c, "data": data} if c["files_id"] == files_id and c["n"] == n else c for c in coll.documents]
