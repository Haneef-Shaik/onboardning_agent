"""The one response envelope: `{success, data, error, meta}` (PRD §10).

Build it with `ok(data)` or `fail(code, message)`. Exactly one of `data`/`error` carries the answer:
success means no error, failure means an error and no data.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Self

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

    @classmethod
    def model_parametrized_name(cls, params: tuple[Any, ...]) -> str:
        """`Envelope[Health]` is `HealthEnvelope` in OpenAPI and the TS client; `Envelope[None]` is `ErrorEnvelope`."""
        return f"{_type_name(params[0])}Envelope"


def _type_name(tp: Any) -> str:
    if tp is None or tp is types.NoneType:
        return "Error"
    origin = typing.get_origin(tp)
    if origin in (list, tuple, set, frozenset):
        args = typing.get_args(tp)
        return f"{_type_name(args[0]) if args else 'Any'}List"
    return getattr(tp, "__name__", "Any")


ErrorEnvelope = Envelope[None]


def ok[DataT](data: DataT, *, meta: Meta | None = None) -> Envelope[DataT]:
    return Envelope[DataT](success=True, data=data, meta=meta or Meta())


def fail(
    code: ErrorCode,
    message: str,
    *,
    details: tuple[ErrorDetail, ...] = (),
    meta: Meta | None = None,
) -> Envelope[None]:
    return ErrorEnvelope(
        success=False,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta or Meta(),
    )
