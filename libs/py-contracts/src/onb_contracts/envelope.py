"""The one response envelope: `{success, data, error, meta}` (PRD §10).

Build it with `ok(data)` or `fail(code, message)`. Exactly one of `data`/`error` carries the answer:
success means no error, failure means an error and no data.

Give every response its own named subclass, `class HealthEnvelope(Envelope[Health])`, and use it as
the route's `response_model`: OpenAPI (and so the TS client) then calls it `HealthEnvelope` rather
than `Envelope_Health_`.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onb_contracts.errors import ErrorCode

MAX_PAGE_LIMIT = 1000

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ErrorDetail(BaseModel):
    """One problem with the request: where (`field`, a dotted location) and what (`issue`), never the value."""

    model_config = _FROZEN

    field: str | None = None
    issue: str


class ErrorBody(BaseModel):
    model_config = _FROZEN

    code: ErrorCode
    message: str
    details: tuple[ErrorDetail, ...] = ()


class PageMeta(BaseModel):
    model_config = _FROZEN

    total: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=MAX_PAGE_LIMIT)


class Meta(BaseModel):
    model_config = _FROZEN

    trace_id: str | None = None
    page: PageMeta | None = None


class Envelope[DataT](BaseModel):
    model_config = _FROZEN

    success: bool
    data: DataT | None = None
    error: ErrorBody | None = None
    meta: Meta = Meta()

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if self.success and self.error is not None:
            raise ValueError("a success envelope carries no error")
        if not self.success and self.error is None:
            raise ValueError("a failure envelope must carry an error")
        if not self.success and self.data is not None:
            raise ValueError("a failure envelope carries no data")
        return self


class ErrorEnvelope(Envelope[None]):
    """An error answer: `success` is false, `error` says why, `data` is null."""


def ok[DataT](data: DataT, *, meta: Meta | None = None) -> Envelope[DataT]:
    return Envelope[DataT](success=True, data=data, meta=meta or Meta())


def fail(
    code: ErrorCode,
    message: str,
    *,
    details: tuple[ErrorDetail, ...] = (),
    meta: Meta | None = None,
) -> ErrorEnvelope:
    return ErrorEnvelope(
        success=False,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta or Meta(),
    )
