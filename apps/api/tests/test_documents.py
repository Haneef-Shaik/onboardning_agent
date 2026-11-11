"""`POST /hires/{hire_id}/documents`: scans stored encrypted, only metadata answered (HIRE-2; PRD FR-P3,
§7.10 Documents, §10 Hires).

The app runs over the in-memory database (`onb_api.testing_mongo`) with the real keyring, tenant
scoping, hire store and document bucket. Every scan here is synthetic bytes behind a real file signature.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_api.documents.bucket import CHUNKS_COLLECTION, FILES_COLLECTION
from onb_api.documents.meta import META_COLLECTION
from onb_api.documents.service import DocumentService
from onb_api.documents.upload import read_capped
from onb_api.errors import AppError
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts import ErrorCode
from onb_contracts.auth import Role
from onb_contracts.documents import MAX_DOCUMENT_BYTES
from onb_crypto import DecryptionError
from onb_data import AuditOutcome, AuditQuery, tenant_scope

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
HIRE = "h_acme_001"  # seed_v1: Ravi's report, no Aadhaar consent recorded

MARKER = b"SYNTHETIC-PAN-SCAN-BQVPS4821K"
PNG = b"\x89PNG\r\n\x1a\n" + MARKER + secrets.token_bytes(4096)
JPEG = b"\xff\xd8\xff\xe0" + MARKER + secrets.token_bytes(2048)
PDF = b"%PDF-1.7\n" + MARKER + secrets.token_bytes(1024)
SCANS = {"image/png": PNG, "image/jpeg": JPEG, "application/pdf": PDF}


def _headers(actor: tuple[str, str, Role], media_type: str | None = "image/png") -> dict[str, str]:
    headers = bearer(token_for(*actor))
    return headers if media_type is None else {**headers, "Content-Type": media_type}


def _upload(
    client: TestClient,
    *,
    hire_id: str = HIRE,
    document_type: str = "pan",
    data: bytes = PNG,
    media_type: str | None = "image/png",
    actor: tuple[str, str, Role] = HR,
) -> Any:
    return client.post(
        f"/hires/{hire_id}/documents",
        params={"type": document_type},
        content=data,
        headers=_headers(actor, media_type),
    )


def _read(documents: DocumentService, org: str, hire_id: str, document_id: str) -> bytes:
    async def scoped() -> bytes:
        with tenant_scope(org):
            return await documents.read(org, hire_id=hire_id, document_id=document_id)

    return asyncio.run(scoped())


# --- the upload: encrypted bytes, metadata answer -----------------------------------------------------------


@pytest.mark.parametrize("media_type", sorted(SCANS))
def test_hr_uploads_a_scan_and_gets_only_its_metadata(client: TestClient, media_type: str) -> None:
    response = _upload(client, data=SCANS[media_type], media_type=media_type)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert set(data) == {"id", "hire_id", "type", "uploaded_at", "verified"}
    assert data["id"].startswith("doc_")
    assert (data["hire_id"], data["type"], data["verified"]) == (HIRE, "pan", False)
    assert MARKER.decode() not in response.text


def test_hr_admin_may_upload_too(client: TestClient) -> None:
    assert _upload(client, actor=ADMIN).status_code == 201


def test_the_stored_bytes_are_encrypted_and_open_only_for_the_hire(
    client: TestClient, documents: DocumentService, memory_db: MemoryDatabase
) -> None:
    """Box 1 through the route: GridFS holds ciphertext; it opens for this hire and no other."""
    document_id = _upload(client).json()["data"]["id"]
    raw = b"".join(bytes(c["data"]) for c in sorted(memory_db.raw(CHUNKS_COLLECTION), key=lambda c: c["n"]))
    assert raw != PNG
    assert MARKER not in raw
    assert MARKER not in repr(list(memory_db.all_documents())).encode()
    assert _read(documents, "org_acme", HIRE, document_id) == PNG
    with pytest.raises(LookupError):
        _read(documents, "org_acme", "h_acme_002", document_id)


def test_moving_a_scan_to_another_hire_in_the_database_does_not_open_it(
    client: TestClient, documents: DocumentService, memory_db: MemoryDatabase
) -> None:
    document_id = _upload(client).json()["data"]["id"]
    files = memory_db[FILES_COLLECTION]
    files.documents = [{**f, "metadata": {**f["metadata"], "hire_id": "h_acme_002"}} for f in files.documents]
    with pytest.raises(DecryptionError):
        _read(documents, "org_acme", "h_acme_002", document_id)


def test_documents_meta_holds_only_ids_type_uploaded_at_and_verified(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    """Box 3 through the route."""
    data = _upload(client).json()["data"]
    (stored,) = memory_db.raw(META_COLLECTION)
    assert set(stored) == {"_id", "org_id", "hire_id", "type", "uploaded_at", "verified"}
    assert (stored["_id"], stored["org_id"], stored["hire_id"], stored["type"], stored["verified"]) == (
        data["id"],
        "org_acme",
        HIRE,
        "pan",
        False,
    )


def test_an_upload_is_audited_without_content(
    client: TestClient, hire_audit: InMemoryAuditLog, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    document_id = _upload(client).json()["data"]["id"]
    with tenant_scope("org_acme"):
        entries = asyncio.run(hire_audit.list(AuditQuery(), page=1, limit=50)).entries
    assert [(e.action, e.target, e.outcome) for e in entries] == [
        ("document.upload", f"hire:{HIRE}/document/{document_id}", AuditOutcome.SUCCESS)
    ]
    assert MARKER.decode() not in caplog.text


def test_an_upload_whose_audit_fails_leaves_nothing(
    client: TestClient, hire_audit: InMemoryAuditLog, memory_db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(**_: Any) -> str:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(hire_audit, "audit", broken)
    assert _upload(client).status_code == 500
    for collection in (META_COLLECTION, FILES_COLLECTION, CHUNKS_COLLECTION):
        assert memory_db.raw(collection) == [], collection


def test_an_upload_whose_audit_and_metadata_undo_both_fail_still_removes_the_bytes(
    client: TestClient,
    hire_audit: InMemoryAuditLog,
    memory_db: MemoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("store down")

    monkeypatch.setattr(hire_audit, "audit", broken)
    monkeypatch.setattr(memory_db[META_COLLECTION], "delete_one", broken)
    assert _upload(client).status_code == 500
    assert memory_db.raw(FILES_COLLECTION) == []
    assert memory_db.raw(CHUNKS_COLLECTION) == []
    assert "metadata failed: RuntimeError" in caplog.text


def test_an_upload_whose_metadata_write_fails_leaves_no_bytes(client: TestClient, memory_db: MemoryDatabase) -> None:
    memory_db[META_COLLECTION].fail_next_insert = RuntimeError("database went away")
    assert _upload(client).status_code == 500
    assert memory_db.raw(FILES_COLLECTION) == []
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_an_org_without_keys_gets_them_on_its_first_upload(client: TestClient, memory_db: MemoryDatabase) -> None:
    assert memory_db.raw("org_keys") == []
    assert _upload(client).status_code == 201
    assert {k["org_id"] for k in memory_db.raw("org_keys")} == {"org_acme"}


# --- box 2: oversized files and disallowed MIME types are rejected -------------------------------------------


def test_an_oversized_scan_is_refused_with_413(client: TestClient, memory_db: MemoryDatabase) -> None:
    too_big = b"\x89PNG\r\n\x1a\n" + bytes(MAX_DOCUMENT_BYTES)
    response = _upload(client, data=too_big)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert memory_db.raw(CHUNKS_COLLECTION) == []
    assert memory_db.raw(META_COLLECTION) == []


def test_a_scan_of_exactly_the_limit_is_accepted(client: TestClient) -> None:
    at_limit = b"\x89PNG\r\n\x1a\n" + bytes(MAX_DOCUMENT_BYTES - 8)
    assert _upload(client, data=at_limit).status_code == 201


def test_an_oversized_stream_without_a_content_length_is_refused(client: TestClient) -> None:
    """A chunked body has no Content-Length: the limit is enforced on the bytes read."""

    def body() -> Any:
        yield b"\x89PNG\r\n\x1a\n"
        for _ in range(6):
            yield bytes(1024 * 1024)

    response = client.post(
        f"/hires/{HIRE}/documents", params={"type": "pan"}, content=body(), headers=_headers(HR, "image/png")
    )
    assert response.status_code == 413, response.text


def test_an_understated_content_length_does_not_lift_the_limit() -> None:
    """The header is a hint for an early refusal; the bytes actually read decide."""

    async def body() -> AsyncIterator[bytes]:
        yield b"\x89PNG\r\n\x1a\n"
        yield bytes(MAX_DOCUMENT_BYTES)

    with pytest.raises(AppError) as refused:
        asyncio.run(read_capped(body(), declared_length="10", limit=MAX_DOCUMENT_BYTES))
    assert refused.value.code is ErrorCode.PAYLOAD_TOO_LARGE


@pytest.mark.parametrize("declared", [str(MAX_DOCUMENT_BYTES + 1), "99999999999"])
def test_an_overstated_content_length_is_refused_before_reading(declared: str) -> None:
    read: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        read.append(1)
        yield b"\x89PNG\r\n\x1a\n"

    with pytest.raises(AppError) as refused:
        asyncio.run(read_capped(body(), declared_length=declared, limit=MAX_DOCUMENT_BYTES))
    assert refused.value.code is ErrorCode.PAYLOAD_TOO_LARGE
    assert read == []  # refused on the header, before the body was read


def test_one_byte_past_the_limit_is_refused_when_counting() -> None:
    """The counted limit is exact: `limit` bytes pass, `limit + 1` do not (no Content-Length to go by)."""

    def body(size: int) -> AsyncIterator[bytes]:
        async def chunks() -> AsyncIterator[bytes]:
            yield bytes(size - 1)
            yield b"\x00"

        return chunks()

    assert asyncio.run(read_capped(body(10), declared_length=None, limit=10)) == bytes(10)
    with pytest.raises(AppError) as refused:
        asyncio.run(read_capped(body(11), declared_length=None, limit=10))
    assert refused.value.code is ErrorCode.PAYLOAD_TOO_LARGE


@pytest.mark.parametrize("declared", [None, "", "abc", "-5", "\u00b2"])
def test_a_missing_or_malformed_content_length_falls_back_to_counting(declared: str | None) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"abc"
        yield b"def"

    assert asyncio.run(read_capped(body(), declared_length=declared, limit=10)) == b"abcdef"


@pytest.mark.parametrize(
    "media_type",
    [
        "text/plain",
        "text/html",
        "image/gif",
        "image/svg+xml",
        "application/zip",
        "application/octet-stream",
        "application/x-msdownload",
        "image/heic",
    ],
)
def test_a_disallowed_media_type_is_refused(client: TestClient, memory_db: MemoryDatabase, media_type: str) -> None:
    response = _upload(client, media_type=media_type)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [
        {"field": "header.content-type", "issue": "Must be one of application/pdf, image/jpeg, image/png"}
    ]
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_a_missing_content_type_is_refused(client: TestClient) -> None:
    response = _upload(client, media_type=None)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == "header.content-type"


@pytest.mark.parametrize(
    ("declared", "data"),
    [
        ("image/png", JPEG),
        ("image/jpeg", PDF),
        ("application/pdf", PNG),
        ("image/png", b"MZ\x90\x00 an executable"),
        ("application/pdf", b"<html><script>alert(1)</script></html>"),
    ],
    ids=["jpeg-as-png", "pdf-as-jpeg", "png-as-pdf", "exe-as-png", "html-as-pdf"],
)
def test_content_that_is_not_the_declared_type_is_refused(
    client: TestClient, memory_db: MemoryDatabase, declared: str, data: bytes
) -> None:
    response = _upload(client, data=data, media_type=declared)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [{"field": "body", "issue": f"The content is not a {declared} file"}]
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_media_type_parameters_and_case_are_tolerated(client: TestClient) -> None:
    assert _upload(client, media_type="Image/PNG; name=scan").status_code == 201


def test_an_empty_scan_is_refused(client: TestClient) -> None:
    response = _upload(client, data=b"")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [{"field": "body", "issue": "The document is empty"}]


@pytest.mark.parametrize("document_type", ["passport_scan", "", "AADHAAR"])
def test_an_unknown_document_type_is_refused(client: TestClient, document_type: str) -> None:
    assert _upload(client, document_type=document_type).status_code == 422


def test_a_missing_document_type_is_refused(client: TestClient) -> None:
    response = client.post(f"/hires/{HIRE}/documents", content=PNG, headers=_headers(HR))
    assert response.status_code == 422


# --- Aadhaar scans need the hire's Aadhaar consent ----------------------------------------------------------


def test_an_aadhaar_scan_needs_the_hires_aadhaar_consent(client: TestClient) -> None:
    response = _upload(client, document_type="aadhaar")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [
        {"field": "query.type", "issue": "This hire has not consented to storing an Aadhaar"}
    ]


def test_an_aadhaar_consent_refusal_is_audited_as_denied(client: TestClient, hire_audit: InMemoryAuditLog) -> None:
    _upload(client, document_type="aadhaar")
    with tenant_scope("org_acme"):
        entries = asyncio.run(hire_audit.list(AuditQuery(), page=1, limit=50)).entries
    assert [(e.action, e.target, e.outcome, e.reason) for e in entries] == [
        ("document.upload", f"hire:{HIRE}/document", AuditOutcome.DENIED, "no aadhaar consent")
    ]


def test_a_consent_flag_forged_on_the_hire_does_not_open_the_gate(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    """`new_hires` is agent-writable through mcp-mongo; the gate reads the vault's `pii_consent` instead."""
    hires = memory_db["new_hires"]
    hires.documents = [
        {**h, "id_proof": {"aadhaar_consent": True, "alternative_id": None}} if h["_id"] == HIRE else h
        for h in hires.documents
    ]
    assert _upload(client, document_type="aadhaar").status_code == 422
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_another_hires_consent_does_not_count(client: TestClient) -> None:
    _hire_with_aadhaar(client)  # a different hire consents
    assert _upload(client, document_type="aadhaar").status_code == 422


def test_an_aadhaar_scan_of_a_consenting_hire_is_stored_under_the_aadhaar_key(
    client: TestClient, documents: DocumentService, memory_db: MemoryDatabase
) -> None:
    hire_id = _hire_with_aadhaar(client)
    response = _upload(client, hire_id=hire_id, document_type="aadhaar")
    assert response.status_code == 201, response.text
    (files,) = memory_db.raw(FILES_COLLECTION)
    assert files["metadata"]["key_purpose"] == "aadhaar"
    assert _read(documents, "org_acme", hire_id, response.json()["data"]["id"]) == PNG


# --- who may upload, and to which hire ----------------------------------------------------------------------


def test_a_manager_may_not_upload(client: TestClient, memory_db: MemoryDatabase) -> None:
    response = _upload(client, actor=MANAGER)
    assert response.status_code == 403
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_without_a_token_it_is_401(client: TestClient) -> None:
    response = client.post(f"/hires/{HIRE}/documents", params={"type": "pan"}, content=PNG)
    assert response.status_code == 401


def test_another_orgs_hire_answers_like_a_missing_one(client: TestClient, memory_db: MemoryDatabase) -> None:
    foreign = _upload(client, hire_id="h_globex_001")
    missing = _upload(client, hire_id="h_nobody")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert memory_db.raw(CHUNKS_COLLECTION) == []


def test_an_org_b_upload_lands_in_org_b_only(client: TestClient, memory_db: MemoryDatabase) -> None:
    assert _upload(client, hire_id="h_globex_001", actor=GLOBEX_HR).status_code == 201
    stored = memory_db.raw(META_COLLECTION) + memory_db.raw(FILES_COLLECTION) + memory_db.raw(CHUNKS_COLLECTION)
    assert {d["org_id"] for d in stored} == {"org_globex"}


@pytest.mark.parametrize("bad_id", ["nothire", "h_" + "x" * 70, "h_a.b"])
def test_a_malformed_hire_id_is_refused(client: TestClient, bad_id: str) -> None:
    assert _upload(client, hire_id=bad_id).status_code in {404, 422}


def test_without_a_document_service_the_route_answers_503(users: Any, registry: Any, hires: Any) -> None:
    from onb_api import create_app
    from onb_api.testing import TEST_SETTINGS

    app = create_app(auth=TEST_SETTINGS, users=users, audit=InMemoryAuditLog(), agents=registry, hires=hires)
    with TestClient(app) as bare:
        assert _upload(bare).status_code == 503


# --- helpers ------------------------------------------------------------------------------------------------


def _hire_with_aadhaar(client: TestClient) -> str:
    body = {
        "name": "Neha Verma",
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-07",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": "neha.docs@example.com", "aadhaar": "9999 0000 4202"},
        "aadhaar_consent": {"purpose": "statutory onboarding records"},
    }
    response = client.post("/hires", json=body, headers=bearer(token_for(*HR)))
    assert response.status_code == 201, response.text
    hire_id: str = response.json()["data"]["id"]
    return hire_id
