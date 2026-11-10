"""`GET /audit`: HR_ADMIN reads the org's audit log, with filters and pagination (AUD-1 box 2; PRD §10, §7.6).

HR and MANAGER get 403; HR_ADMIN gets 200. Records come only from the caller's org (the token's), and
no query parameter can name an org.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, bearer, token_for
from onb_contracts.audit import MAX_AUDIT_SKIP, AuditActorType, AuditOutcome
from onb_contracts.auth import Role
from onb_data import MAX_AUDIT_SKIP as DATA_MAX_SKIP
from onb_data import Actor, ActorType, tenant_scope
from onb_data import AuditOutcome as DataOutcome

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_ADMIN = ("u_globex_admin", "org_globex", Role.HR_ADMIN)
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


def _headers(actor: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*actor))


def _seed(log: InMemoryAuditLog) -> None:
    rows: list[tuple[str, Actor, str, str, DataOutcome, str | None]] = [
        ("org_acme", Actor.user("u_acme_hr1"), "pii.reveal", "hire:h_1/pan", DataOutcome.SUCCESS, "documents"),
        ("org_acme", Actor.agent("db_agent"), "mcp.tool_call", "find:pii_vault", DataOutcome.DENIED, "vault"),
        ("org_acme", Actor.user("u_acme_hr1"), "run.manual_complete", "run:r_1/t_2", DataOutcome.SUCCESS, "done"),
        ("org_globex", Actor.user("u_globex_hr1"), "pii.reveal", "hire:h_9/pan", DataOutcome.SUCCESS, None),
    ]

    async def run() -> None:
        for minutes, (org, actor, action, target, outcome, reason) in enumerate(rows):
            log.now = T0 + timedelta(minutes=minutes)
            log.trace = f"{minutes + 1:032x}"
            with tenant_scope(org):
                await log.audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)

    asyncio.run(run())


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    log = InMemoryAuditLog()
    _seed(log)
    return log


def _get(client: TestClient, actor: tuple[str, str, Role], **params: Any) -> Any:
    return client.get("/audit", params=params, headers=_headers(actor))


# --- Box 2: roles ----------------------------------------------------------------------------------


def test_hr_admin_gets_200_on_get_audit(client: TestClient) -> None:
    response = _get(client, ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True


@pytest.mark.parametrize("actor", [HR, MANAGER], ids=["HR", "MANAGER"])
def test_hr_and_manager_get_403_on_get_audit(client: TestClient, actor: tuple[str, str, Role]) -> None:
    response = _get(client, actor)
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["data"] is None


@pytest.mark.parametrize("actor", [HR, MANAGER], ids=["HR", "MANAGER"])
def test_a_refused_caller_never_reaches_the_audit_log(
    app: FastAPI, client: TestClient, actor: tuple[str, str, Role]
) -> None:
    log: InMemoryAuditLog = app.state.audit
    _get(client, actor)
    assert log.list_calls == 0


def test_get_audit_without_a_token_is_401(client: TestClient) -> None:
    assert client.get("/audit").status_code == 401


def test_get_audit_without_an_audit_log_is_503(users: Any) -> None:
    with TestClient(create_app(auth=TEST_SETTINGS, users=users)) as plain:
        response = _get(plain, ADMIN)
    assert response.status_code == 503


# --- Records, org scope, filters, pagination -----------------------------------------------------


@pytest.fixture
def seeded(users: Any, audit_log: InMemoryAuditLog) -> Any:
    with TestClient(create_app(auth=TEST_SETTINGS, users=users, audit=audit_log)) as test_client:
        yield test_client


def test_hr_admin_sees_the_orgs_records_newest_first(seeded: TestClient) -> None:
    body = _get(seeded, ADMIN).json()
    actions = [record["action"] for record in body["data"]]
    assert actions == ["run.manual_complete", "mcp.tool_call", "pii.reveal"]
    first = body["data"][0]
    assert first == {
        "id": first["id"],
        "org_id": "org_acme",
        "actor": {"type": "user", "id": "u_acme_hr1"},
        "action": "run.manual_complete",
        "target": "run:r_1/t_2",
        "outcome": "success",
        "reason": "done",
        "trace_id": f"{3:032x}",
        "at": "2026-11-01T08:02:00Z",
    }
    assert body["meta"]["page"] == {"total": 3, "page": 1, "limit": 50}


def test_another_orgs_admin_sees_only_their_own_records(seeded: TestClient) -> None:
    body = _get(seeded, GLOBEX_ADMIN).json()
    assert [(r["org_id"], r["target"]) for r in body["data"]] == [("org_globex", "hire:h_9/pan")]


@pytest.mark.parametrize("name", ["org_id", "orgId", "tenant", "org"])
def test_an_org_in_the_query_is_ignored(seeded: TestClient, name: str) -> None:
    body = _get(seeded, ADMIN, **{name: "org_globex"}).json()
    assert {record["org_id"] for record in body["data"]} == {"org_acme"}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"actor_type": "agent"}, ["mcp.tool_call"]),
        ({"actor_id": "u_acme_hr1"}, ["run.manual_complete", "pii.reveal"]),
        ({"action": "pii.reveal"}, ["pii.reveal"]),
        ({"outcome": "denied"}, ["mcp.tool_call"]),
        ({"target": "run:r_1/t_2"}, ["run.manual_complete"]),
        ({"since": "2026-11-01T08:01:00Z"}, ["run.manual_complete", "mcp.tool_call"]),
        ({"until": "2026-11-01T08:01:00Z"}, ["mcp.tool_call", "pii.reveal"]),
        ({"actor_id": "u_acme_hr1", "action": "pii.reveal"}, ["pii.reveal"]),
    ],
    ids=["actor_type", "actor_id", "action", "outcome", "target", "since", "until", "combined"],
)
def test_filters(seeded: TestClient, params: dict[str, str], expected: list[str]) -> None:
    body = _get(seeded, ADMIN, **params).json()
    assert [record["action"] for record in body["data"]] == expected
    assert body["meta"]["page"]["total"] == len(expected)


def test_the_filters_reach_the_log_as_an_audit_query(seeded: TestClient, audit_log: InMemoryAuditLog) -> None:
    _get(seeded, ADMIN, actor_type="agent", outcome="denied", since="2026-11-01T08:00:00+05:30", page=2, limit=1)
    assert audit_log.last_list is not None
    query, page, limit = audit_log.last_list
    assert query.actor_type is ActorType.AGENT
    assert query.outcome is DataOutcome.DENIED
    assert query.since == datetime(2026, 11, 1, 2, 30, tzinfo=UTC)
    assert (page, limit) == (2, 1)


def test_pagination(seeded: TestClient) -> None:
    pages = [_get(seeded, ADMIN, page=n, limit=2).json() for n in (1, 2, 3)]
    assert [[r["action"] for r in p["data"]] for p in pages] == [
        ["run.manual_complete", "mcp.tool_call"],
        ["pii.reveal"],
        [],
    ]
    assert [p["meta"]["page"] for p in pages] == [
        {"total": 3, "page": 1, "limit": 2},
        {"total": 3, "page": 2, "limit": 2},
        {"total": 3, "page": 3, "limit": 2},
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"limit": 0},
        {"limit": 201},
        {"actor_type": "robot"},
        {"outcome": "maybe"},
        {"action": "Not An Action"},
        {"actor_id": "has space"},
        {"target": "x" * 257},
        {"target": "hire:jane@example.com"},
        {"actor_id": "jane@example.com"},
        {"page": 10**30},
        {"page": 52, "limit": 200},
        {"page": 10_002, "limit": 1},
        {"since": "2026-11-01T08:00:00"},
        {"since": "yesterday"},
        {"since": "2026-11-02T00:00:00Z", "until": "2026-11-01T00:00:00Z"},
    ],
    ids=lambda p: "+".join(p),
)
def test_bad_query_parameters_are_422(seeded: TestClient, audit_log: InMemoryAuditLog, params: dict[str, Any]) -> None:
    response = _get(seeded, ADMIN, **params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert audit_log.list_calls == 0


def test_contract_enums_match_the_data_layer() -> None:
    assert {m.value for m in AuditActorType} == {m.value for m in ActorType}
    assert {m.value for m in AuditOutcome} == {m.value for m in DataOutcome}


def test_startup_makes_the_audit_indexes(users: Any) -> None:
    log = InMemoryAuditLog()
    with TestClient(create_app(auth=TEST_SETTINGS, users=users, audit=log)):
        pass
    assert log.indexes_ensured == 1


def test_the_last_allowed_page_is_10000_records_deep(seeded: TestClient) -> None:
    response = _get(seeded, ADMIN, page=51, limit=200)
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_the_api_contract_bounds_match_the_data_layer() -> None:
    assert MAX_AUDIT_SKIP == DATA_MAX_SKIP
