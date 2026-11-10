"""Stable error codes and their HTTP statuses (PRD §10).

Clients branch on `ErrorCode` values, so a value is never renamed or reused; add a new one instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


ERROR_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.BAD_REQUEST: 400,
        ErrorCode.UNAUTHENTICATED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.METHOD_NOT_ALLOWED: 405,
        ErrorCode.CONFLICT: 409,
        ErrorCode.PAYLOAD_TOO_LARGE: 413,
        ErrorCode.VALIDATION_ERROR: 422,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.INTERNAL_ERROR: 500,
        ErrorCode.SERVICE_UNAVAILABLE: 503,
    }
)

_BY_STATUS: Mapping[int, ErrorCode] = MappingProxyType({status: code for code, status in ERROR_STATUS.items()})


def code_for_status(status: int) -> ErrorCode:
    """The code for an HTTP error status; an unlisted 4xx is BAD_REQUEST and an unlisted 5xx INTERNAL_ERROR."""
    if not 400 <= status < 600:
        raise ValueError(f"{status} is not an HTTP error status")
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    return ErrorCode.BAD_REQUEST if status < 500 else ErrorCode.INTERNAL_ERROR
