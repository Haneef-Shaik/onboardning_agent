"""`GET /templates/drafts` pages like `/hires` and `/runs` (FIX-1; PRD §10 Templates, §12 Latency).

PERF-1 measured every list route and found this one returning every draft of the org at once
(`docs/evidence/PERF-1/latency.md`). It takes `page` and `limit`, answers `meta.page` with the total, and
bounds both, the way `/hires` and `/runs` do. Tenancy of the paged list is in `tools/tests_xt`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_api.templates.routes import DEFAULT_DRAFT_LIMIT, MAX_DRAFT_LIMIT, MAX_DRAFT_PAGE
from onb_api.testing import bearer, token_for
from onb_contracts.auth import Role

HR: Final = ("u_acme_hr1", "org_acme", Role.HR)
DRAFTS: Final = 7
SEED_TEMPLATE: Final = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"


def seed_template() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    return data


def _headers() -> dict[str, str]:
    return bearer(token_for(*HR))


def _create_drafts(client: TestClient, count: int) -> set[str]:
    ids = set()
    for n in range(count):
        template = {**seed_template(), "key": f"paging-{n}", "name": f"Paging {n}"}
        created = client.post("/templates/drafts", json={"template": template}, headers=_headers())
        assert created.status_code == 201, created.text
        ids.add(created.json()["data"]["id"])
    return ids


def _list(client: TestClient, **params: Any) -> Any:
    return client.get("/templates/drafts", params=params, headers=_headers())


def test_the_draft_list_pages_and_reports_the_total(client: TestClient) -> None:
    ids = _create_drafts(client, DRAFTS)
    everything = _list(client, limit=MAX_DRAFT_LIMIT).json()["data"]
    pages = [_list(client, page=page, limit=3).json() for page in (1, 2, 3, 4)]
    assert [len(p["data"]) for p in pages] == [3, 3, 1, 0]
    assert all(p["meta"]["page"] == {"total": DRAFTS, "page": n, "limit": 3} for n, p in enumerate(pages, 1))
    paged = [draft["id"] for p in pages for draft in p["data"]]
    assert paged == [draft["id"] for draft in everything], "pages follow the list's order, newest first"
    assert set(paged) == ids


def test_the_draft_list_has_a_default_limit(client: TestClient) -> None:
    _create_drafts(client, 2)
    body = _list(client).json()
    assert body["meta"]["page"] == {"total": 2, "page": 1, "limit": DEFAULT_DRAFT_LIMIT}
    assert len(body["data"]) == 2


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": MAX_DRAFT_LIMIT + 1}, {"page": 0}, {"page": MAX_DRAFT_PAGE + 1}, {"limit": "x"}],
)
def test_out_of_range_paging_is_422(client: TestClient, params: dict[str, Any]) -> None:
    response = _list(client, **params)
    assert response.status_code == 422, response.text
