"""Document upload contracts (HIRE-2; PRD FR-P3, §7.10 Documents, §10 `POST /hires/{id}/documents`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from onb_contracts.documents import (
    ALLOWED_DOCUMENT_MEDIA_TYPES,
    MAX_DOCUMENT_BYTES,
    DocumentMeta,
    DocumentMetaEnvelope,
    DocumentType,
)
from pydantic import ValidationError


def test_the_document_types_are_the_government_ids() -> None:
    assert {t.value for t in DocumentType} == {"aadhaar", "pan", "passport", "voter_id", "driving_licence"}


def test_only_pdf_jpeg_and_png_are_allowed() -> None:
    assert frozenset({"application/pdf", "image/jpeg", "image/png"}) == ALLOWED_DOCUMENT_MEDIA_TYPES


def test_the_size_limit_is_five_mebibytes() -> None:
    assert MAX_DOCUMENT_BYTES == 5 * 1024 * 1024


def test_document_meta_carries_only_ids_type_upload_time_and_verified() -> None:
    """Box 3, contract side: what anyone (agents included) is shown of a scan."""
    assert set(DocumentMeta.model_fields) == {"id", "hire_id", "type", "uploaded_at", "verified"}
    meta = DocumentMeta(
        id="doc_1", hire_id="h_1", type=DocumentType.PAN, uploaded_at=datetime(2026, 11, 11, tzinfo=UTC), verified=False
    )
    assert DocumentMetaEnvelope(success=True, data=meta).data == meta


@pytest.mark.parametrize("extra", ["content_type", "filename", "size", "org_id", "token"])
def test_document_meta_refuses_any_other_field(extra: str) -> None:
    with pytest.raises(ValidationError):
        DocumentMeta.model_validate(
            {
                "id": "doc_1",
                "hire_id": "h_1",
                "type": "pan",
                "uploaded_at": "2026-11-11T00:00:00Z",
                "verified": False,
                extra: "x",
            }
        )
