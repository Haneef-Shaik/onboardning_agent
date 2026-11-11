"""Request bodies on `/templates` are bounded (TPL-2 security review H1): an oversized body is 413 in the
envelope before it is parsed, whether it declares its length or streams it in chunks."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from onb_api.body_limit import TEMPLATE_BODY_LIMIT
from onb_api.testing import bearer, token_for
from onb_contracts.auth import Role

HR = bearer(token_for("u_acme_hr1", "org_acme", Role.HR))
OVERSIZED = b'{"template": {"key": "x", "name": "' + b"a" * (TEMPLATE_BODY_LIMIT + 1) + b'"}}'


def _chunks(data: bytes, size: int = 64 * 1024) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


@pytest.mark.parametrize("url", ["/templates/drafts", "/templates/drafts/tdr_" + "0" * 32])
def test_an_oversized_declared_body_is_413(client: TestClient, url: str) -> None:
    method = "POST" if url.endswith("drafts") else "PUT"
    headers = {**HR, "content-type": "application/json"}
    response = client.request(method, url, content=OVERSIZED, headers=headers)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_an_oversized_streamed_body_is_413(client: TestClient) -> None:
    headers = {**HR, "content-type": "application/json"}
    response = client.post("/templates/drafts", content=_chunks(OVERSIZED), headers=headers)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_a_body_under_the_limit_reaches_the_route(client: TestClient) -> None:
    response = client.post("/templates/drafts", json={"template": {"key": "x"}}, headers=HR)
    assert response.status_code == 422  # parsed and validated, not cut off


def test_the_limit_fits_the_largest_valid_template() -> None:
    # 100 tasks with 16 000 characters of inputs each, plus their other fields.
    assert TEMPLATE_BODY_LIMIT >= 2 * 1024 * 1024


def test_routes_outside_templates_are_not_limited_by_it(client: TestClient) -> None:
    body = b'{"email": "' + b"a" * (TEMPLATE_BODY_LIMIT + 1) + b'", "password": "x"}'
    response = client.post("/auth/login", content=body, headers={"content-type": "application/json"})
    assert response.status_code != 413
