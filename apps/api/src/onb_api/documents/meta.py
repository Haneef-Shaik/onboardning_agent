"""`documents_meta`: all that anyone but the uploader's encrypted bucket knows of a scan (PRD FR-P3).

A record is exactly `{_id: "doc_…", org_id, hire_id, type, uploaded_at, verified}`: no file name, size,
media type, token or digest. Agents read and write this collection through mcp-mongo (db_agent's
manifest lists it, REG-1); the bytes live only in the encrypted bucket, which no MCP user can reach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from onb_contracts.documents import DocumentMeta, DocumentType
from onb_data import DataStore, TenantRepository

__all__ = ["META_COLLECTION", "DocumentMetaStore"]

META_COLLECTION: Final = "documents_meta"


class DocumentMetaStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def _repo(self) -> TenantRepository:
        return self._store.tenant(META_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(META_COLLECTION, [("org_id", 1), ("hire_id", 1)])

    async def add(self, *, document_id: str, hire_id: str, document_type: DocumentType, now: datetime) -> DocumentMeta:
        record = DocumentMeta(id=document_id, hire_id=hire_id, type=document_type, uploaded_at=now, verified=False)
        await self._repo().insert_one(
            {
                "_id": record.id,
                "hire_id": record.hire_id,
                "type": record.type.value,
                "uploaded_at": record.uploaded_at,
                "verified": record.verified,
            }
        )
        return record

    async def delete(self, document_id: str) -> None:
        await self._repo().delete_one({"_id": document_id})
