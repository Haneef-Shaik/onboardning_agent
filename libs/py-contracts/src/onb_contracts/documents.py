"""Government document scans of a hire (PRD FR-P3, §7.10 Documents, §10 `POST /hires/{id}/documents`; HIRE-2).

HR uploads a scan as the raw request body (`Content-Type` one of `ALLOWED_DOCUMENT_MEDIA_TYPES`, at most
`MAX_DOCUMENT_BYTES`), naming its `type` in the query. The bytes are encrypted before they are stored.
Everyone, agents included, sees only `DocumentMeta`: ids, `type`, `uploaded_at` and `verified`. No file
name, size or media type is kept in the metadata, and no route returns the bytes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from onb_contracts.envelope import Envelope

__all__ = [
    "ALLOWED_DOCUMENT_MEDIA_TYPES",
    "DOCUMENT_ID_PATTERN",
    "MAX_DOCUMENT_BYTES",
    "DocumentMeta",
    "DocumentMetaEnvelope",
    "DocumentType",
    "DocumentsEnvelope",
]

MAX_DOCUMENT_BYTES: Final = 5 * 1024 * 1024
ALLOWED_DOCUMENT_MEDIA_TYPES: Final = frozenset({"application/pdf", "image/jpeg", "image/png"})
# Document ids are made by the server: `doc_` + 24 hex.
DOCUMENT_ID_PATTERN: Final = r"^doc_[0-9a-f]{24}$"


class DocumentType(StrEnum):
    """What a scan shows: Aadhaar or PAN (PRD FR-P3), or the alternative ID a hire gives instead of
    Aadhaar (`hires.AlternativeIdType`)."""

    AADHAAR = "aadhaar"
    PAN = "pan"
    PASSPORT = "passport"
    VOTER_ID = "voter_id"
    DRIVING_LICENCE = "driving_licence"


class DocumentMeta(BaseModel):
    """A scan as anyone sees it (FR-P3): `documents_meta` holds exactly this, plus `org_id`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    hire_id: str
    type: DocumentType
    uploaded_at: datetime
    verified: bool


class DocumentMetaEnvelope(Envelope[DocumentMeta]):
    """One uploaded scan's metadata."""


class DocumentsEnvelope(Envelope[list[DocumentMeta]]):
    """The scans of one hire, oldest first (UI-4): metadata only."""
