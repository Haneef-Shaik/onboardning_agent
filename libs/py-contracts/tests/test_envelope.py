"""The one response envelope every API answer uses (PRD §10)."""

import pytest
from onb_contracts import (
    ERROR_STATUS,
    Envelope,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    Meta,
    PageMeta,
    code_for_status,
    fail,
    ok,
)
from pydantic import BaseModel, ValidationError


class Thing(BaseModel):
    name: str


def test_ok_wraps_data_with_success_and_no_error() -> None:
    env = ok(Thing(name="a"))
    assert env.model_dump(mode="json") == {
        "success": True,
        "data": {"name": "a"},
        "error": None,
        "meta": {"trace_id": None, "page": None},
    }


def test_fail_carries_a_code_and_no_data() -> None:
    env = fail(ErrorCode.NOT_FOUND, "Not found")
    dumped = env.model_dump(mode="json")
    assert dumped["success"] is False
    assert dumped["data"] is None
    assert dumped["error"] == {"code": "NOT_FOUND", "message": "Not found", "details": []}


def test_every_envelope_has_exactly_the_four_keys() -> None:
    assert set(ok(Thing(name="a")).model_dump(mode="json")) == {"success", "data", "error", "meta"}
    assert set(fail(ErrorCode.INTERNAL_ERROR, "x").model_dump(mode="json")) == {"success", "data", "error", "meta"}


def test_page_meta_travels_in_meta() -> None:
    env = ok([Thing(name="a")], meta=Meta(page=PageMeta(total=1, page=1, limit=20)))
    assert env.model_dump(mode="json")["meta"]["page"] == {"total": 1, "page": 1, "limit": 20}


@pytest.mark.parametrize(
    ("total", "page", "limit"),
    [(-1, 1, 20), (0, 0, 20), (0, 1, 0), (0, 1, 1001)],
)
def test_page_meta_rejects_impossible_values(total: int, page: int, limit: int) -> None:
    with pytest.raises(ValidationError):
        PageMeta(total=total, page=page, limit=limit)


def test_success_with_an_error_is_rejected() -> None:
    with pytest.raises(ValidationError, match="success"):
        Envelope[Thing](success=True, data=None, error=ErrorBody(code=ErrorCode.CONFLICT, message="x"))


def test_failure_without_an_error_is_rejected() -> None:
    with pytest.raises(ValidationError, match="error"):
        Envelope[Thing](success=False, data=None, error=None)


def test_failure_with_data_is_rejected() -> None:
    with pytest.raises(ValidationError, match="data"):
        Envelope[Thing](
            success=False,
            data=Thing(name="a"),
            error=ErrorBody(code=ErrorCode.CONFLICT, message="x"),
        )


def test_envelopes_are_immutable() -> None:
    env = ok(Thing(name="a"))
    with pytest.raises(ValidationError):
        env.success = False


def test_parametrised_envelopes_have_readable_schema_names() -> None:
    assert Envelope[Thing].__name__ == "ThingEnvelope"
    assert Envelope[None].__name__ == "ErrorEnvelope"
    assert Envelope[list[Thing]].__name__ == "ThingListEnvelope"


def test_error_details_name_a_field_and_an_issue_only() -> None:
    detail = ErrorDetail(field="body.email", issue="missing")
    assert detail.model_dump() == {"field": "body.email", "issue": "missing"}


def test_error_codes_are_stable_strings() -> None:
    # Clients branch on these values; renaming one is a breaking change.
    assert {c.value for c in ErrorCode} == {
        "BAD_REQUEST",
        "UNAUTHENTICATED",
        "FORBIDDEN",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "CONFLICT",
        "PAYLOAD_TOO_LARGE",
        "VALIDATION_ERROR",
        "RATE_LIMITED",
        "INTERNAL_ERROR",
        "SERVICE_UNAVAILABLE",
    }


def test_every_error_code_has_an_http_status() -> None:
    assert set(ERROR_STATUS) == set(ErrorCode)
    assert all(400 <= status < 600 for status in ERROR_STATUS.values())


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, ErrorCode.BAD_REQUEST),
        (401, ErrorCode.UNAUTHENTICATED),
        (403, ErrorCode.FORBIDDEN),
        (404, ErrorCode.NOT_FOUND),
        (405, ErrorCode.METHOD_NOT_ALLOWED),
        (409, ErrorCode.CONFLICT),
        (413, ErrorCode.PAYLOAD_TOO_LARGE),
        (422, ErrorCode.VALIDATION_ERROR),
        (429, ErrorCode.RATE_LIMITED),
        (500, ErrorCode.INTERNAL_ERROR),
        (503, ErrorCode.SERVICE_UNAVAILABLE),
        (418, ErrorCode.BAD_REQUEST),
        (502, ErrorCode.INTERNAL_ERROR),
    ],
)
def test_code_for_status_maps_known_and_falls_back_by_class(status: int, code: ErrorCode) -> None:
    assert code_for_status(status) is code


@pytest.mark.parametrize("status", [200, 302, 99, 600])
def test_code_for_status_refuses_non_error_statuses(status: int) -> None:
    with pytest.raises(ValueError, match="error status"):
        code_for_status(status)
