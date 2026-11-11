"""Checks on an uploaded scan before anything is stored (PRD FR-P3; HIRE-2 box 2).

- **Media type.** The `Content-Type` must be one of `ALLOWED_DOCUMENT_MEDIA_TYPES` (parameters and case
  ignored), and the bytes must start with that type's file signature, so a renamed executable, HTML page
  or another image format is refused whatever it claims to be.
- **Size.** A declared `Content-Length` over the limit is refused before the body is read; the bytes
  actually read are counted too and refused one byte past the limit, so a chunked body or an understated
  header cannot lift it. The body is held in memory only (never spooled to a temporary file), and only
  until it is encrypted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final

from onb_contracts import ErrorCode, ErrorDetail
from onb_contracts.documents import ALLOWED_DOCUMENT_MEDIA_TYPES

from onb_api.errors import AppError

__all__ = ["check_content", "media_type_of", "read_capped"]

# The leading bytes each allowed type must start with.
_SIGNATURES: Final = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
_ALLOWED_TEXT: Final = ", ".join(sorted(ALLOWED_DOCUMENT_MEDIA_TYPES))


def media_type_of(content_type: str | None) -> str:
    """The allowed media type a `Content-Type` header names; 422 for any other or none."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_DOCUMENT_MEDIA_TYPES:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="header.content-type", issue=f"Must be one of {_ALLOWED_TEXT}"),),
        )
    return media_type


def check_content(media_type: str, data: bytes) -> None:
    """422 when the bytes are empty or do not carry `media_type`'s signature."""
    if not data:
        raise AppError(ErrorCode.VALIDATION_ERROR, details=(ErrorDetail(field="body", issue="The document is empty"),))
    if not data.startswith(_SIGNATURES[media_type]):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="body", issue=f"The content is not a {media_type} file"),),
        )


def _too_large(limit: int) -> AppError:
    return AppError(ErrorCode.PAYLOAD_TOO_LARGE, f"A document is at most {limit // (1024 * 1024)} MiB")


def _declared(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


async def read_capped(stream: AsyncIterator[bytes], *, declared_length: str | None, limit: int) -> bytes:
    """The whole body, at most `limit` bytes; 413 as soon as it is known to be longer."""
    declared = _declared(declared_length)
    if declared is not None and declared > limit:
        raise _too_large(limit)
    received = bytearray()
    async for chunk in stream:
        if len(received) + len(chunk) > limit:
            raise _too_large(limit)
        received.extend(chunk)
    return bytes(received)
