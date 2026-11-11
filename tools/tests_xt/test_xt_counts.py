"""Counts and list totals never include the other org (T-3 box 3; PRD §7.9, trap: list totals).

Org B holds more audit records than org A, and the seed's tenant collections differ in size per org,
so a total that counted org B would be a wrong number. Each source is checked from both sides: org
A's numbers equal org A's seed, org B's equal org B's, and filters matching both orgs count one org.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_api.testing import bearer, token_for
from onb_contracts.auth import Role

from .conftest import McpServer, World
from .mcp_client import READ, call
from .world import ORG_A, ORG_B, audit_rows, tenant_documents

ADMINS = {ORG_A: ("u_acme_hradmin", ORG_A, Role.HR_ADMIN), ORG_B: ("u_globex_xt_admin", ORG_B, Role.HR_ADMIN)}
ORGS = [ORG_A, ORG_B]


def _total(api: TestClient, org: str, params: dict[str, Any]) -> tuple[int, int]:
    """(meta.page.total, records returned) of GET /audit for the org's admin."""
    body = api.get("/audit", params=params, headers=bearer(token_for(*ADMINS[org]))).json()
    assert {r["org_id"] for r in body["data"]} <= {org}
    return body["meta"]["page"]["total"], len(body["data"])


@pytest.mark.parametrize("org", ORGS)
def test_the_audit_list_total_counts_one_org(api: TestClient, xt_world: World, org: str) -> None:
    own = len(audit_rows(org))
    assert _total(api, org, {"limit": 100}) == (own, own)
    for limit in (1, 2):
        total, returned = _total(api, org, {"limit": limit})
        assert (total, returned) == (own, limit)
    # "pii.reveal" is in both orgs' logs: each admin's total counts their own only.
    reveals = sum(1 for row in audit_rows(org) if row[3] == "pii.reveal")
    assert _total(api, org, {"action": "pii.reveal"}) == (reveals, reveals)


@pytest.mark.parametrize("org", ORGS)
@pytest.mark.parametrize("collection", sorted(READ))
def test_mcp_counts_and_find_totals_count_one_org(
    mcp_mongo: McpServer, xt_world: World, org: str, collection: str
) -> None:
    own = len(tenant_documents(org)[collection])

    def ok(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return call(mcp_mongo.url, mcp_mongo.key, org, tool, args).ok()

    assert ok("count", {"collection": collection}) == {"count": own}
    assert ok("find", {"collection": collection, "filter": {}, "limit": 100})["count"] == own
    pipeline = [{"$count": "n"}]
    assert ok("aggregate", {"collection": collection, "pipeline": pipeline})["documents"] == (
        [{"n": own}] if own else []
    )


def test_both_orgs_have_data_in_the_counted_collections() -> None:
    """Guards the counts above: they prove nothing if org B had no documents to leak."""
    assert len(audit_rows(ORG_B)) > len(audit_rows(ORG_A)) > 0
    for collection in sorted(READ):
        assert tenant_documents(ORG_A)[collection]
        assert tenant_documents(ORG_B)[collection]
