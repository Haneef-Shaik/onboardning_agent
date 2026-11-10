"""Every error leaves the API as the envelope with a stable code, and no internals (PRD §10)."""

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from onb_api import AppError
from onb_contracts import ErrorCode, RequestModel
from pydantic import Field, field_validator

LEAK_MARKER = "hunter2-aadhaar-1234-5678-9012"


class Shape(RequestModel):
    name: str
    size: int


def _add_failing_routes(app: FastAPI) -> None:
    async def boom() -> None:
        raise RuntimeError(f"database password is {LEAK_MARKER}")

    def boom_sync() -> None:
        raise KeyError(LEAK_MARKER)

    async def conflict() -> None:
        raise AppError(ErrorCode.CONFLICT, "That template key is taken")

    async def teapot() -> None:
        raise HTTPException(status_code=418, detail=f"leaky {LEAK_MARKER}")

    async def create(shape: Shape) -> None:
        return None

    app.add_api_route("/test/boom", boom)
    app.add_api_route("/test/boom-sync", boom_sync)
    app.add_api_route("/test/conflict", conflict)
    app.add_api_route("/test/teapot", teapot)
    app.add_api_route("/test/shapes", create, methods=["POST"])


@pytest.fixture
def failing(app: FastAPI, client: TestClient) -> TestClient:
    _add_failing_routes(app)
    return client


def _assert_error_envelope(body: dict[str, object], code: ErrorCode) -> dict[str, object]:
    assert set(body) == {"success", "data", "error", "meta"}
    assert body["success"] is False
    assert body["data"] is None
    error = body["error"]
    assert isinstance(error, dict)
    assert error["code"] == code.value
    return error


@pytest.mark.parametrize("path", ["/test/boom", "/test/boom-sync"])
def test_unhandled_exception_returns_the_envelope_with_a_stable_code_and_no_stack_trace(
    failing: TestClient, path: str
) -> None:
    response = failing.get(path)

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    error = _assert_error_envelope(response.json(), ErrorCode.INTERNAL_ERROR)
    assert error["message"] == "Internal server error"
    assert error["details"] == []
    for leak in ("Traceback", "RuntimeError", "KeyError", LEAK_MARKER, 'File "', ".py"):
        assert leak not in response.text


def test_unhandled_exception_log_names_the_type_but_not_the_message(
    failing: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="onb_api"):
        failing.get("/test/boom")

    records = [r for r in caplog.records if r.name == "onb_api.errors"]
    assert len(records) == 1
    logged = records[0].getMessage()
    assert "RuntimeError" in logged
    assert "/test/boom" in logged
    assert LEAK_MARKER not in logged
    assert records[0].exc_info is None


def test_app_error_keeps_its_code_status_and_message(failing: TestClient) -> None:
    response = failing.get("/test/conflict")

    assert response.status_code == 409
    error = _assert_error_envelope(response.json(), ErrorCode.CONFLICT)
    assert error["message"] == "That template key is taken"


def test_http_exception_uses_the_code_for_its_status_and_not_its_detail(failing: TestClient) -> None:
    response = failing.get("/test/teapot")

    assert response.status_code == 418
    error = _assert_error_envelope(response.json(), ErrorCode.BAD_REQUEST)
    assert LEAK_MARKER not in response.text
    assert error["message"] == "Bad request"


def test_unknown_route_is_not_found_in_the_envelope(client: TestClient) -> None:
    response = client.get("/no/such/route")

    assert response.status_code == 404
    _assert_error_envelope(response.json(), ErrorCode.NOT_FOUND)


def test_wrong_method_is_method_not_allowed_and_keeps_the_allow_header(client: TestClient) -> None:
    response = client.delete("/health")

    assert response.status_code == 405
    assert "GET" in response.headers["allow"]
    _assert_error_envelope(response.json(), ErrorCode.METHOD_NOT_ALLOWED)


def test_validation_error_names_fields_but_never_echoes_input(failing: TestClient) -> None:
    response = failing.post("/test/shapes", json={"size": LEAK_MARKER, "org_id": "org-b"})

    assert response.status_code == 422
    error = _assert_error_envelope(response.json(), ErrorCode.VALIDATION_ERROR)
    assert error["message"] == "The request is not valid"
    details = error["details"]
    assert isinstance(details, list)
    fields = {d["field"] for d in details}
    assert fields == {"body.name", "body.size", "body.<extra>"}
    assert all(set(d) == {"field", "issue"} for d in details)
    assert LEAK_MARKER not in response.text
    assert "org-b" not in response.text


def test_malformed_json_is_a_validation_error(failing: TestClient) -> None:
    response = failing.post("/test/shapes", content=b"{not json", headers={"content-type": "application/json"})

    assert response.status_code == 422
    _assert_error_envelope(response.json(), ErrorCode.VALIDATION_ERROR)
    assert "{not json" not in response.text


class Checked(RequestModel):
    aadhaar: str
    tags: dict[str, int] = Field(default_factory=dict)
    kind: Literal["a", "b"] = "a"

    @field_validator("aadhaar")
    @classmethod
    def _shape(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError(f"bad aadhaar {value}")  # a careless validator echoing the value
        return value


@pytest.fixture
def checked(app: FastAPI, client: TestClient) -> TestClient:
    async def create(body: Checked) -> None:
        return None

    app.add_api_route("/test/checked", create, methods=["POST"])
    return client


def _details(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    details = response_json["error"]["details"]
    assert isinstance(details, list)
    return details


def test_a_validator_message_carrying_the_value_is_not_echoed(checked: TestClient) -> None:
    response = checked.post("/test/checked", json={"aadhaar": LEAK_MARKER})

    assert response.status_code == 422
    assert LEAK_MARKER not in response.text
    _assert_error_envelope(response.json(), ErrorCode.VALIDATION_ERROR)
    assert _details(response.json()) == [{"field": "body.aadhaar", "issue": "Invalid value"}]


def test_user_chosen_keys_never_appear_in_field_locations(checked: TestClient) -> None:
    pii_key = "jane.doe@example.com"
    body = {"aadhaar": "123", "tags": {pii_key: "x", "999988887777": "y", "janedoe": "z"}, pii_key: 1}
    response = checked.post("/test/checked", json=body)

    assert response.status_code == 422
    for leak in (pii_key, "999988887777", "janedoe"):
        assert leak not in response.text
    assert {d["field"] for d in _details(response.json())} == {"body.tags.*", "body.<extra>"}


def test_issue_texts_are_fixed_per_error_type(checked: TestClient) -> None:
    response = checked.post("/test/checked", json={"kind": "AADHAAR999988887777"})

    assert "AADHAAR999988887777" not in response.text
    issues = {d["field"]: d["issue"] for d in _details(response.json())}
    assert issues == {"body.aadhaar": "Field required", "body.kind": "Not an allowed value"}


def test_an_http_exception_with_a_non_error_status_passes_through(app: FastAPI, client: TestClient) -> None:
    async def not_modified() -> None:
        raise HTTPException(status_code=304, headers={"etag": '"v1"'})

    app.add_api_route("/test/not-modified", not_modified)
    response = client.get("/test/not-modified")

    assert response.status_code == 304
    assert response.headers["etag"] == '"v1"'
    assert response.content == b""


def test_a_failure_after_the_response_started_is_re_raised_without_its_message(app: FastAPI) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"partial"
        raise ValueError(f"mid-stream {LEAK_MARKER}")

    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    app.add_api_route("/test/stream", stream)
    with TestClient(app) as client, pytest.raises(RuntimeError) as raised:
        client.get("/test/stream")

    assert LEAK_MARKER not in str(raised.value)
    assert "ValueError" in str(raised.value)
    assert raised.value.__suppress_context__
    assert raised.value.__cause__ is None
