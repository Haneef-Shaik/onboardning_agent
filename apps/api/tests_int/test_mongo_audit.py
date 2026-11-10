"""The audit log against a real MongoDB: `AuditLog` writes, `GET /audit` reads (AUD-1; PRD §7.6, §7.9).

Each test uses a scratch database on the compose Mongo, dropped afterwards. Records of two orgs are
written through `AuditLog.audit(...)` inside real spans, then read back both through the API and with
the root client, so the test does not trust the log to report on itself.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.testing import TEST_SETTINGS, InMemoryUserStore, bearer, token_for
from onb_contracts.auth import Role
from onb_data import Actor, AuditLog, AuditOutcome, UnknownCollectionError, connect, tenant_scope
from opentelemetry.sdk.trace import TracerProvider
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
ROWS = [
    ("org_acme", Actor.user("u_acme_hr1"), "pii.reveal", "hire:h_1/pan", AuditOutcome.SUCCESS),
    ("org_acme", Actor.agent("db_agent"), "mcp.tool_call", "find:pii_vault", AuditOutcome.DENIED),
    ("org_globex", Actor.user("u_globex_hr1"), "pii.reveal", "hire:h_9/pan", AuditOutcome.SUCCESS),
    ("org_acme", Actor.user("u_acme_hr1"), "run.manual_complete", "run:r_1/t_2", AuditOutcome.SUCCESS),
]
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_aud1_{uuid.uuid4().hex[:12]}"
    yield _uri(root_client), name
    root_client.drop_database(name)


def _write(scratch: tuple[str, str]) -> list[str]:
    """Write ROWS one minute apart, each inside its own span; return the spans' trace ids."""
    tracer = TracerProvider().get_tracer("aud-1-int")
    minutes = iter(range(len(ROWS)))

    def clock() -> datetime:
        return T0 + timedelta(minutes=next(minutes))

    async def main() -> list[str]:
        store = connect(*scratch)
        log = AuditLog(store, clock=clock)
        trace_ids = []
        try:
            for org_id, actor, action, target, outcome in ROWS:
                with tracer.start_as_current_span(action) as span, tenant_scope(org_id):
                    await log.audit(actor=actor, action=action, target=target, outcome=outcome, reason="it")
                trace_ids.append(f"{span.get_span_context().trace_id:032x}")
        finally:
            await store.close()
        return trace_ids

    return asyncio.run(main())


@pytest.fixture
def api(scratch: tuple[str, str]) -> Iterator[TestClient]:
    store = connect(*scratch)
    app = create_app(auth=TEST_SETTINGS, users=InMemoryUserStore(), audit=AuditLog(store))
    with TestClient(app) as client:
        yield client


def test_records_land_in_mongo_with_org_and_the_spans_trace_id(scratch: tuple[str, str], root_client: Client) -> None:
    trace_ids = _write(scratch)
    stored = list(root_client[scratch[1]]["audit_log"].find({}, sort=[("at", 1)]))
    assert [(d["org_id"], d["action"]) for d in stored] == [(row[0], row[2]) for row in ROWS]
    assert [d["trace_id"] for d in stored] == trace_ids
    assert len(set(trace_ids)) == len(ROWS)
    assert all(len(d["_id"]) == 32 for d in stored)
    assert stored[0]["actor"] == {"type": "user", "id": "u_acme_hr1"}
    assert stored[0]["at"].replace(tzinfo=UTC) == T0


def test_hr_admin_reads_only_their_orgs_records_newest_first(scratch: tuple[str, str], api: TestClient) -> None:
    trace_ids = _write(scratch)
    response = api.get("/audit", headers=bearer(token_for(*ADMIN)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["action"] for r in body["data"]] == ["run.manual_complete", "mcp.tool_call", "pii.reveal"]
    assert {r["org_id"] for r in body["data"]} == {"org_acme"}
    assert body["data"][0]["trace_id"] == trace_ids[3]
    assert body["data"][0]["at"] == "2026-11-01T08:03:00Z"
    assert body["meta"]["page"] == {"total": 3, "page": 1, "limit": 50}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"outcome": "denied"}, ["mcp.tool_call"]),
        ({"actor_type": "user", "action": "pii.reveal"}, ["pii.reveal"]),
        ({"since": "2026-11-01T08:01:00Z", "until": "2026-11-01T08:02:00Z"}, ["mcp.tool_call"]),
        ({"page": 2, "limit": 2}, ["pii.reveal"]),
    ],
    ids=["outcome", "actor+action", "window", "page-2"],
)
def test_filters_and_pages_in_mongo(
    scratch: tuple[str, str], api: TestClient, params: dict[str, Any], expected: list[str]
) -> None:
    _write(scratch)
    body = api.get("/audit", params=params, headers=bearer(token_for(*ADMIN))).json()
    assert [r["action"] for r in body["data"]] == expected


@pytest.mark.parametrize("role", [Role.HR, Role.MANAGER], ids=lambda r: r.value)
def test_hr_and_manager_get_403_against_the_real_log(scratch: tuple[str, str], api: TestClient, role: Role) -> None:
    _write(scratch)
    response = api.get("/audit", headers=bearer(token_for("u_acme_x", "org_acme", role)))
    assert response.status_code == 403


def test_startup_makes_the_org_prefixed_index(scratch: tuple[str, str], api: TestClient, root_client: Client) -> None:
    indexes = root_client[scratch[1]]["audit_log"].index_information()
    assert indexes["org_at"]["key"] == [("org_id", 1), ("at", -1), ("_id", -1)]


def test_the_store_hands_out_no_writable_view_of_the_audit_log(scratch: tuple[str, str]) -> None:
    store = connect(*scratch)
    for handout in (store.tenant, store.shared, store.queue):
        with pytest.raises(UnknownCollectionError):
            handout("audit_log")
    asyncio.run(store.close())
