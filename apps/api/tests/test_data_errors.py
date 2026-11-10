"""Data-layer errors that reach a route become envelope answers (T-1 handoff, PRD §7.9).

No tenant context is 401, a foreign or missing id is 404 (never 403: no existence leak), and an
attempt to name another org is 400. The answer carries the fixed message, never the error's text.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_contracts import Envelope
from onb_data import NoTenantContextError, NotFoundError, TenantViolationError


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NoTenantContextError, 401, "UNAUTHENTICATED"),
        (NotFoundError, 404, "NOT_FOUND"),
        (TenantViolationError, 400, "BAD_REQUEST"),
    ],
)
def test_a_data_error_becomes_its_envelope_answer(
    app: FastAPI, client: TestClient, error: type[Exception], status: int, code: str
) -> None:
    async def fails() -> Envelope[None]:
        raise error("internal detail org_secret")

    app.add_api_route("/test/data-error", fails, include_in_schema=False)
    response = client.get("/test/data-error")
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert "org_secret" not in response.text
