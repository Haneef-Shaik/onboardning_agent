"""Every error leaves the API as the envelope with a stable code and nothing internal (PRD §10).

- `AppError(code, message)` is how route code fails on purpose; the message is shown to the client.
- `HTTPException` (from FastAPI/Starlette: 404, 405, ...) gets the code for its status and that
  code's fixed message. Its `detail` is never shown.
- Validation errors list the failing fields and issues, never the submitted values (`validation`).
- An `HTTPException` with a status under 400 (304, a redirect) passes through without the envelope.
- Data-layer errors (PRD §7.9): no tenant context is 401, a missing or foreign id is 404 (never
  403, so nothing leaks about other orgs), naming another org is 400. Their text is never shown.
- Anything else is `INTERNAL_ERROR` with a fixed message. The server log and the span get the
  exception type and route only: messages and tracebacks can carry personal data (PRD §5.5).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from onb_contracts import ERROR_STATUS, ErrorCode, ErrorDetail, code_for_status, fail
from onb_data import NoTenantContextError, NotFoundError, TenantViolationError
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from onb_api.responses import current_meta
from onb_api.validation import safe_details

_log = logging.getLogger("onb_api.errors")

MIN_ERROR_STATUS = 400

MESSAGES: Mapping[ErrorCode, str] = {
    ErrorCode.BAD_REQUEST: "Bad request",
    ErrorCode.UNAUTHENTICATED: "Authentication required",
    ErrorCode.FORBIDDEN: "Not allowed",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.METHOD_NOT_ALLOWED: "Method not allowed",
    ErrorCode.CONFLICT: "Conflict",
    ErrorCode.PAYLOAD_TOO_LARGE: "Request too large",
    ErrorCode.VALIDATION_ERROR: "The request is not valid",
    ErrorCode.RATE_LIMITED: "Too many requests",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
    ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
}

# Headers a client needs to act on an error; everything else an exception carries is dropped.
_KEPT_HEADERS = frozenset({"allow", "retry-after", "www-authenticate"})


class AppError(Exception):
    """A deliberate failure: `message` is client-safe text, `code` picks the HTTP status."""

    def __init__(self, code: ErrorCode, message: str | None = None, *, details: Sequence[ErrorDetail] = ()) -> None:
        self.code = code
        self.message = message or MESSAGES[code]
        self.details = tuple(details)
        super().__init__(code.value)


def error_response(
    code: ErrorCode,
    message: str | None = None,
    *,
    status: int | None = None,
    details: Sequence[ErrorDetail] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    envelope = fail(code, message or MESSAGES[code], details=tuple(details), meta=current_meta())
    return JSONResponse(
        envelope.model_dump(mode="json"),
        status_code=status or ERROR_STATUS[code],
        headers=dict(headers) if headers else None,
    )


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # registered for AppError only
        raise exc
    return error_response(exc.code, exc.message, details=exc.details)


async def _http_error(_: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):  # registered for StarletteHTTPException only
        raise exc
    if exc.status_code < MIN_ERROR_STATUS:
        # A deliberate non-error answer (304, a redirect): no envelope, headers as given, no body.
        return Response(status_code=exc.status_code, headers=exc.headers)
    headers = {k: v for k, v in (exc.headers or {}).items() if k.lower() in _KEPT_HEADERS}
    return error_response(code_for_status(exc.status_code), status=exc.status_code, headers=headers)


_DATA_ERRORS: Mapping[type[Exception], ErrorCode] = {
    NoTenantContextError: ErrorCode.UNAUTHENTICATED,
    NotFoundError: ErrorCode.NOT_FOUND,
    TenantViolationError: ErrorCode.BAD_REQUEST,
}


async def _data_error(request: Request, exc: Exception) -> JSONResponse:
    code = next((c for kind, c in _DATA_ERRORS.items() if isinstance(exc, kind)), None)
    if code is None:  # registered for the _DATA_ERRORS types only
        raise exc
    headers = None
    if code is ErrorCode.UNAUTHENTICATED:
        # Routes answer 401 through the token dependency first; reaching here means a route used
        # tenant data without it, which is a server bug worth a log line.
        route = getattr(request.scope.get("route"), "path", None) or "<unmatched>"
        _log.warning("tenant data used without a tenant context in %s %s", request.method, route)
        headers = {"WWW-Authenticate": "Bearer"}
    return error_response(code, headers=headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registered for RequestValidationError only
        raise exc
    details = safe_details(exc.errors(), _body_type(request))
    return error_response(ErrorCode.VALIDATION_ERROR, details=details)


def _body_type(request: Request) -> Any | None:
    """The type the matched route validates its body against, to tell declared fields from client keys."""
    body_field = getattr(request.scope.get("route"), "body_field", None)
    return getattr(getattr(body_field, "field_info", None), "annotation", None)


class UnhandledErrorMiddleware:
    """Turns any exception that escapes the routes into the `INTERNAL_ERROR` envelope.

    It sits inside the OTel middleware, so the server span is current here and gets marked failed.
    An exception after the response started cannot be answered. It is re-raised as a bare
    `RuntimeError` naming only the type, so the OTel middleware, Starlette and uvicorn (which record
    or log what reaches them) never see the original message or its frames.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            _record_unhandled(exc, scope)
            if started:
                raise RuntimeError(f"unhandled {type(exc).__name__} after the response started") from None
            await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send)


def _record_unhandled(exc: Exception, scope: Scope) -> None:
    kind = type(exc).__name__
    route = scope.get("route")
    path = getattr(route, "path", None) or "<unmatched>"
    location = _raise_site(exc)
    _log.error("unhandled %s in %s %s at %s", kind, scope.get("method", "?"), path, location)
    span = trace.get_current_span()
    span.set_attribute("error.type", kind)
    span.set_status(Status(StatusCode.ERROR, kind))


def _raise_site(exc: BaseException) -> str:
    tb = exc.__traceback__
    if tb is None:
        return "?"
    while tb.tb_next is not None:
        tb = tb.tb_next
    return f"{tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno}"


def install_error_handling(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for kind in _DATA_ERRORS:
        app.add_exception_handler(kind, _data_error)
    app.add_middleware(UnhandledErrorMiddleware)
