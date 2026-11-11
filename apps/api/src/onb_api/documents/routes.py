"""`POST /hires/{hire_id}/documents` (PRD §10 Hires & runs, FR-P3; HIRE-2).

| Route | Roles |
|---|---|
| `POST /hires/{hire_id}/documents?type=<DocumentType>` | HR, HR_ADMIN |

The scan is the raw request body, its `Content-Type` one of `ALLOWED_DOCUMENT_MEDIA_TYPES`, at most
`MAX_DOCUMENT_BYTES` (413 beyond). The answer is the scan's metadata only. A role outside the list is
403 before anything is read; another org's hire is 404 like a hire that does not exist. Bodies are
never logged.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from onb_contracts import Envelope, ErrorCode
from onb_contracts.auth import Role
from onb_contracts.documents import ALLOWED_DOCUMENT_MEDIA_TYPES, DocumentMeta, DocumentMetaEnvelope, DocumentType
from onb_contracts.hires import HIRE_ID_PATTERN

from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.documents.service import DocumentService
from onb_api.errors import AppError
from onb_api.responses import respond

__all__ = ["document_service", "router"]

router = APIRouter(tags=["documents"])

Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]
HireId = Annotated[str, Path(max_length=66, pattern=HIRE_ID_PATTERN)]

# The body is read as a stream, so it is described here rather than by a parameter.
_BINARY_BODY: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "description": "The scan's bytes",
        "content": {
            media: {"schema": {"type": "string", "format": "binary"}} for media in sorted(ALLOWED_DOCUMENT_MEDIA_TYPES)
        },
    }
}


def document_service(request: Request) -> DocumentService:
    service: DocumentService | None = request.app.state.documents
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return service


Documents = Annotated[DocumentService, Depends(document_service)]


@router.post(
    "/hires/{hire_id}/documents",
    response_model=DocumentMetaEnvelope,
    status_code=201,
    operation_id="uploadHireDocument",
    openapi_extra=_BINARY_BODY,
)
async def upload_hire_document(
    principal: Hr,
    documents: Documents,
    hire_id: HireId,
    document_type: Annotated[DocumentType, Query(alias="type")],
    request: Request,
) -> Envelope[DocumentMeta]:
    record = await documents.upload(
        principal,
        hire_id,
        document_type,
        content_type=request.headers.get("content-type"),
        content_length=request.headers.get("content-length"),
        body=request.stream(),
    )
    return respond(record)
