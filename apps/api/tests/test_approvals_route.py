"""`GET /approvals` and `POST /approvals/{id}/decision` (HIL-1; PRD FR-H2, FR-H3, U5, §10 Approvals).

The unit-test app (conftest): the approval service over the hires' in-memory database, publishing to the run
outbox stand-in and auditing to `audit_log`. The same decision write over the real bus and Mongo, with the
Telegram path and the resume, is in `apps/orchestrator/tests_int`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_agents.approvals import AlreadyDecidedError, ApprovalDecider, ApprovalSpec, ApprovalStore
from onb_agents.runs.testing import OutboxStandIn
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.auth import Role
from onb_contracts.notify import Decision
from onb_data import AuditQuery, tenant_scope

MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
HR = ("u_acme_hr1", "org_acme", Role.HR)
HR2 = ("u_acme_hr2", "org_acme", Role.HR)
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
PACKET = {
    "summary": "A MacBook Pro 14 and Bitbucket access, both standard for an engineer.",
    "recommendation": "approve",
    "risk_flags": [],
    "cited_policies": ["kb_access_policy"],
}


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def _seed(db: MemoryDatabase, *specs: tuple[str, ApprovalSpec], org: str = "org_acme") -> dict[str, str]:
    """Open the approvals (task id → approval id)."""
    store = ApprovalStore(memory_store(db))

    async def go() -> dict[str, str]:
        made: dict[str, str] = {}
        with tenant_scope(org):
            for offset, (_, spec) in enumerate(specs):
                created = await store.create(spec, now=NOW.replace(minute=offset))
                made[spec.task_id] = created["_id"]
                if spec.task_id == "with_packet":
                    await store.set_packet(created["_id"], PACKET)
        return made

    return asyncio.run(go())


def _spec(
    task: str = "manager_approval", approver: str | None = "u_acme_mgr_ravi", role: str | None = None
) -> tuple[str, ApprovalSpec]:
    """An approval of `task` for `approver` (a user id), or for every user of `role`."""
    who = (None, role) if role else (approver, None)
    spec = ApprovalSpec("run_1", task, "h_acme_001", "task", None, who[0], who[1], f"Approve {task}", ["Laptop"], 48)
    return task, spec


def _decide(client: TestClient, approval_id: str, who: tuple[str, str, Role] = MANAGER, **body: Any) -> Any:
    return client.post(f"/approvals/{approval_id}/decision", json={"decision": "approve", **body}, headers=_auth(who))


# --- GET /approvals -------------------------------------------------------------------------------------


def test_a_manager_sees_the_approvals_addressed_to_them_with_the_packet(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    ids = _seed(memory_db, _spec("with_packet"), _spec("other", approver="u_acme_hr1"))
    found = client.get("/approvals", headers=_auth(MANAGER))
    assert found.status_code == 200, found.text
    [record] = found.json()["data"]
    assert record["id"] == ids["with_packet"]
    assert (record["status"], record["kind"], record["approver_id"]) == ("pending", "task", "u_acme_mgr_ravi")
    assert record["packet"] == PACKET
    assert record["items"] == ["Laptop"]
    assert record["decision"] is None
    assert found.json()["meta"]["page"] == {"total": 1, "page": 1, "limit": 50}


def test_a_role_approval_is_in_every_inbox_of_that_role(client: TestClient, memory_db: MemoryDatabase) -> None:
    _seed(memory_db, _spec("for_hr", role="HR"))
    for who in (HR, HR2):
        assert [r["task_id"] for r in client.get("/approvals", headers=_auth(who)).json()["data"]] == ["for_hr"]
    assert client.get("/approvals", headers=_auth(MANAGER)).json()["data"] == []
    assert client.get("/approvals", headers=_auth(ADMIN)).json()["data"] == []


def test_the_status_filter_and_paging(client: TestClient, memory_db: MemoryDatabase) -> None:
    ids = _seed(memory_db, _spec("t1"), _spec("t2"), _spec("t3"))
    assert _decide(client, ids["t1"]).status_code == 200
    pending = client.get("/approvals?status=pending", headers=_auth(MANAGER)).json()
    assert sorted(r["task_id"] for r in pending["data"]) == ["t2", "t3"]
    approved = client.get("/approvals?status=approved", headers=_auth(MANAGER)).json()
    assert [r["task_id"] for r in approved["data"]] == ["t1"]
    first = client.get("/approvals?limit=2", headers=_auth(MANAGER)).json()
    assert [r["task_id"] for r in first["data"]] == ["t3", "t2"]  # newest first
    assert first["meta"]["page"]["total"] == 3
    second = client.get("/approvals?limit=2&page=2", headers=_auth(MANAGER)).json()
    assert [r["task_id"] for r in second["data"]] == ["t1"]


def test_hr_can_read_the_whole_org_and_a_manager_cannot(client: TestClient, memory_db: MemoryDatabase) -> None:
    _seed(memory_db, _spec("t1"), _spec("t2", approver="u_acme_hr1"))
    everything = client.get("/approvals?mine=false", headers=_auth(HR)).json()["data"]
    assert sorted(r["task_id"] for r in everything) == ["t1", "t2"]
    assert len(client.get("/approvals?mine=false", headers=_auth(ADMIN)).json()["data"]) == 2
    refused = client.get("/approvals?mine=false", headers=_auth(MANAGER))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "FORBIDDEN"


def test_another_orgs_approvals_are_not_listed(client: TestClient, memory_db: MemoryDatabase) -> None:
    _seed(memory_db, _spec("t1"))
    assert client.get("/approvals?mine=false", headers=_auth(GLOBEX_HR)).json()["data"] == []
    assert client.get("/approvals", headers=_auth(GLOBEX_HR)).json()["data"] == []


@pytest.mark.parametrize("query", ["status=nonsense", "page=0", "limit=0", "limit=101", "page=101"])
def test_bad_query_parameters_are_422(client: TestClient, query: str) -> None:
    assert client.get(f"/approvals?{query}", headers=_auth(MANAGER)).status_code == 422


# --- POST /approvals/{id}/decision -----------------------------------------------------------------------


def test_the_approver_approves_with_a_comment_and_the_run_is_told(
    client: TestClient, memory_db: MemoryDatabase, run_outbox: OutboxStandIn, audit_log: InMemoryAuditLog
) -> None:
    ids = _seed(memory_db, _spec())
    response = _decide(client, ids["manager_approval"], comment="Looks right")
    assert response.status_code == 200, response.text
    record = response.json()["data"]
    assert record["status"] == "approved"
    assert record["decision"]["decision"] == "approve"
    assert record["decision"]["comment"] == "Looks right"
    assert record["decision"]["channel"] == "web"
    assert record["decision"]["decided_by"] == "u_acme_mgr_ravi"
    [event] = [e for e in run_outbox.published if e["type"] == "approval.decided"]
    assert event["payload"] == {
        "approval_id": ids["manager_approval"],
        "run_id": "run_1",
        "task_id": "manager_approval",
        "decision": "approve",
        "channel": "web",
    }

    async def audited() -> list[Any]:
        with tenant_scope("org_acme"):
            return list((await audit_log.list(AuditQuery(), page=1, limit=10)).entries)

    [entry] = asyncio.run(audited())
    assert (entry.action, entry.actor.id, entry.reason) == ("approval.decide", "u_acme_mgr_ravi", "approve:web")


def test_a_rejection_is_recorded_as_rejected(client: TestClient, memory_db: MemoryDatabase) -> None:
    ids = _seed(memory_db, _spec())
    response = client.post(
        f"/approvals/{ids['manager_approval']}/decision", json={"decision": "reject"}, headers=_auth(MANAGER)
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"
    assert response.json()["data"]["decision"]["comment"] is None


def test_the_second_decision_is_409_and_the_first_stands(
    client: TestClient, memory_db: MemoryDatabase, run_outbox: OutboxStandIn
) -> None:
    ids = _seed(memory_db, _spec())
    assert _decide(client, ids["manager_approval"], comment="first").status_code == 200
    again = client.post(
        f"/approvals/{ids['manager_approval']}/decision", json={"decision": "reject"}, headers=_auth(MANAGER)
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"
    kept = client.get("/approvals?status=approved", headers=_auth(MANAGER)).json()["data"]
    assert [(r["status"], r["decision"]["comment"]) for r in kept] == [("approved", "first")]
    assert len([e for e in run_outbox.published if e["type"] == "approval.decided"]) == 1


def test_a_web_decision_after_a_telegram_press_is_409(
    client: TestClient, memory_db: MemoryDatabase, run_outbox: OutboxStandIn
) -> None:
    """Box 2, one order: the press recorded first (the consumer calls this same write), so the web gets 409."""
    ids = _seed(memory_db, _spec())
    decider = ApprovalDecider(ApprovalStore(memory_store(memory_db)), run_outbox)

    async def press() -> None:
        with tenant_scope("org_acme"):
            await decider.decide(
                ids["manager_approval"],
                decision=Decision.APPROVE,
                comment=None,
                channel="telegram",
                user_id="u_acme_mgr_ravi",
            )

    asyncio.run(press())
    response = _decide(client, ids["manager_approval"])
    assert response.status_code == 409
    [stored] = client.get("/approvals?status=approved", headers=_auth(MANAGER)).json()["data"]
    assert stored["decision"]["channel"] == "telegram"


def test_a_telegram_press_after_a_web_decision_is_refused_and_the_web_decision_stands(
    client: TestClient, memory_db: MemoryDatabase, run_outbox: OutboxStandIn
) -> None:
    """Box 2, the other order."""
    ids = _seed(memory_db, _spec())
    assert _decide(client, ids["manager_approval"]).status_code == 200
    decider = ApprovalDecider(ApprovalStore(memory_store(memory_db)), run_outbox)

    async def press() -> None:
        with tenant_scope("org_acme"), pytest.raises(AlreadyDecidedError):
            await decider.decide(
                ids["manager_approval"],
                decision=Decision.REJECT,
                comment=None,
                channel="telegram",
                user_id="u_acme_mgr_ravi",
            )

    asyncio.run(press())
    [stored] = client.get("/approvals?status=approved", headers=_auth(MANAGER)).json()["data"]
    assert stored["decision"]["channel"] == "web"
    assert len([e for e in run_outbox.published if e["type"] == "approval.decided"]) == 1


def test_only_the_approver_decides_and_anyone_else_gets_404(client: TestClient, memory_db: MemoryDatabase) -> None:
    ids = _seed(memory_db, _spec())
    for who in (HR, ADMIN, GLOBEX_HR):
        response = _decide(client, ids["manager_approval"], who)
        assert response.status_code == 404, (who, response.text)
        assert response.json()["error"]["code"] == "NOT_FOUND"
    still = client.get("/approvals?status=pending", headers=_auth(MANAGER)).json()["data"]
    assert len(still) == 1  # nobody else's decision changed it


def test_an_approval_that_does_not_exist_is_404_like_a_foreign_one(client: TestClient) -> None:
    assert _decide(client, "appr_nobody").status_code == 404


def test_any_user_of_the_approvers_role_may_decide_a_role_approval(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    ids = _seed(memory_db, _spec("for_hr", role="HR"))
    response = _decide(client, ids["for_hr"], HR2)
    assert response.status_code == 200
    assert response.json()["data"]["decision"]["decided_by"] == "u_acme_hr2"
    assert _decide(client, ids["for_hr"], HR).status_code == 409  # a colleague was first


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "maybe"},
        {"decision": "approve", "comment": "x" * 1001},
        {"decision": "approve", "comment": 7},
        {"decision": "approve", "org_id": "org_globex"},  # the org is never an input
        {"decision": "approve", "status": "approved"},
    ],
)
def test_a_malformed_decision_is_422_and_changes_nothing(
    client: TestClient, memory_db: MemoryDatabase, body: dict[str, Any]
) -> None:
    ids = _seed(memory_db, _spec())
    response = client.post(f"/approvals/{ids['manager_approval']}/decision", json=body, headers=_auth(MANAGER))
    assert response.status_code == 422
    assert len(client.get("/approvals?status=pending", headers=_auth(MANAGER)).json()["data"]) == 1


def test_an_id_that_is_not_an_approval_id_is_422(client: TestClient) -> None:
    assert _decide(client, "run_1").status_code == 422


def test_without_an_approval_service_the_routes_answer_503() -> None:
    from onb_api import create_app
    from onb_api.testing import TEST_SETTINGS

    with TestClient(create_app(auth=TEST_SETTINGS)) as bare:
        assert bare.get("/approvals", headers=_auth(MANAGER)).status_code == 503
        assert (
            bare.post("/approvals/appr_x/decision", json={"decision": "approve"}, headers=_auth(MANAGER)).status_code
            == 503
        )
