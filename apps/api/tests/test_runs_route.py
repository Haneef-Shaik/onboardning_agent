"""`POST /runs`: start a run on a pinned, published template version (RUN-1; PRD FR-R1, §7.1, §10).

The app is the unit-test app (conftest): seed_v1's hires in an in-memory database, the template
lifecycle over an in-memory store, and the run service over the same database with the outbox
stand-in (`onb_agents.runs.testing.OutboxStandIn`). Against the real bus and Mongo: tests_int.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_agents.runs import RUN_STARTED, RUN_TASKS, RUNS
from onb_agents.runs.testing import OutboxStandIn
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_data import AuditQuery, tenant_scope

SEED_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def _template(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    return {**data, **changes}


def _publish(client: TestClient, template: dict[str, Any], who: tuple[str, str, Role] = ADMIN) -> int:
    draft = client.post("/templates/drafts", json={"template": template}, headers=_auth(who))
    assert draft.status_code == 201, draft.text
    published = client.post(f"/templates/drafts/{draft.json()['data']['id']}/publish", headers=_auth(who))
    assert published.status_code == 200, published.text
    version: int = published.json()["data"]["version"]
    return version


def _start(client: TestClient, version: int = 1, hire: str = "h_acme_001", who: tuple[str, str, Role] = HR) -> Any:
    body = {"hire_id": hire, "template_key": "engineer-india", "template_version": version}
    return client.post("/runs", json=body, headers=_auth(who))


def test_hr_starts_a_run_on_a_published_version(client: TestClient, run_outbox: OutboxStandIn) -> None:
    _publish(client, _template())
    response = _start(client)
    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["id"].startswith("run_")
    assert (run["hire_id"], run["template_key"], run["template_version"]) == ("h_acme_001", "engineer-india", 1)
    assert run["status"] == "started"
    assert run["dag_hash"].startswith("sha256:")
    assert run["created_by"] == "u_acme_hr1"
    by_id = {t["task_id"]: t for t in run["tasks"]}
    assert by_id["bitbucket_access"]["status"] == "pending"  # the hire is an engineer
    assert by_id["collect_docs"]["due"] == "2026-11-24"
    assert [e["type"] for e in run_outbox.published] == [RUN_STARTED]
    assert run_outbox.published[0]["payload"] == {"run_id": run["id"]}
    assert run_outbox.published[0]["idempotency_key"] == f"run:{run['id']}:started"
    assert run_outbox.published[0]["org_id"] == "org_acme"


def test_a_sales_hire_gets_the_engineer_only_task_skipped_with_a_reason(client: TestClient) -> None:
    _publish(client, _template())
    run = _start(client, hire="h_acme_003", who=ADMIN).json()["data"]
    task = next(t for t in run["tasks"] if t["task_id"] == "bitbucket_access")
    assert task["status"] == "skipped"
    assert "role == 'engineer'" in task["skip_reason"]
    assert "sales" not in task["skip_reason"]  # never the hire's value


def test_the_run_and_its_tasks_are_stored_in_the_callers_org_and_audited(
    client: TestClient, memory_db: MemoryDatabase, audit_log: InMemoryAuditLog
) -> None:
    _publish(client, _template())
    run_id = _start(client).json()["data"]["id"]
    runs = memory_db.raw(RUNS)
    assert [(r["_id"], r["org_id"], r["template_version"]) for r in runs] == [(run_id, "org_acme", 1)]
    assert runs[0]["thread_id"] == f"org_acme:{run_id}"
    assert {t["org_id"] for t in memory_db.raw(RUN_TASKS)} == {"org_acme"}
    assert len(memory_db.raw(RUN_TASKS)) == len(_template()["tasks"])

    async def entries() -> list[Any]:
        with tenant_scope("org_acme"):
            return list((await audit_log.list(AuditQuery(action="run.start"), page=1, limit=10)).entries)

    [entry] = asyncio.run(entries())
    assert (entry.target, entry.outcome.value, entry.actor.id) == (f"run:{run_id}", "success", "u_acme_hr1")


def test_a_manager_may_not_start_a_run(client: TestClient, memory_db: MemoryDatabase) -> None:
    _publish(client, _template())
    response = _start(client, who=MANAGER)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert memory_db.raw(RUNS) == []


def test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    _publish(client, _template())
    first = _start(client).json()["data"]
    newer = _template(name="Engineers, India (v2)")
    newer["tasks"] = [*newer["tasks"], {**newer["tasks"][0], "id": "exit_survey", "depends_on": ["collect_docs"]}]
    assert _publish(client, newer) == 2
    second = _start(client, version=2).json()["data"]
    again = _start(client, version=1).json()["data"]

    stored = {r["_id"]: r for r in memory_db.raw(RUNS)}
    assert stored[first["id"]]["template_version"] == 1
    assert stored[first["id"]]["dag_hash"] == first["dag_hash"] == again["dag_hash"]
    assert "exit_survey" not in {t["task_id"] for t in first["tasks"]}
    assert "exit_survey" not in {t["id"] for t in stored[first["id"]]["dag"]["tasks"]}
    assert second["template_version"] == 2
    assert "exit_survey" in {t["task_id"] for t in second["tasks"]}
    assert second["dag_hash"] != first["dag_hash"]


def test_an_archived_version_starts_no_run(client: TestClient, memory_db: MemoryDatabase) -> None:
    _publish(client, _template())
    archived = client.post("/templates/engineer-india/versions/1/archive", headers=_auth(ADMIN))
    assert archived.status_code == 200
    response = _start(client)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"][0]["issue"].startswith("not_published:")
    assert memory_db.raw(RUNS) == []


@pytest.mark.parametrize(
    ("version", "hire"),
    [(2, "h_acme_001"), (1, "h_acme_999"), (1, "h_globex_001")],
    ids=["unknown-version", "unknown-hire", "other-orgs-hire"],
)
def test_an_unknown_or_foreign_hire_or_version_is_404(
    client: TestClient, memory_db: MemoryDatabase, version: int, hire: str
) -> None:
    _publish(client, _template())
    response = _start(client, version=version, hire=hire)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert memory_db.raw(RUNS) == []


def test_another_orgs_template_version_is_404_like_a_missing_one(client: TestClient) -> None:
    _publish(client, _template())  # org_acme's engineer-india v1
    foreign = _start(client, hire="h_globex_001", who=GLOBEX_HR)
    missing = client.post(
        "/runs",
        json={"hire_id": "h_globex_001", "template_key": "no-such-key", "template_version": 1},
        headers=_auth(GLOBEX_HR),
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]


def test_a_hire_whose_variables_do_not_compile_is_422_naming_the_reason(
    client: TestClient, memory_db: MemoryDatabase
) -> None:
    _publish(client, _template())
    memory_db["new_hires"].documents = [
        {**d, "role": "astronaut"} if d["_id"] == "h_acme_002" else d for d in memory_db["new_hires"].documents
    ]
    response = _start(client, hire="h_acme_002")
    assert response.status_code == 422
    issue = response.json()["error"]["details"][0]["issue"]
    assert issue.startswith("bad_variable:")
    assert issue.count("bad_variable") == 1  # the reason once, not repeated
    assert "astronaut" not in response.text  # the hire's value is never echoed
    assert memory_db.raw(RUNS) == []


@pytest.mark.parametrize(
    "body",
    [
        {"hire_id": "h_acme_001", "template_key": "engineer-india", "template_version": 1, "org_id": "org_globex"},
        {"hire_id": "h_acme_001", "template_key": "engineer-india"},
        {"hire_id": "h_acme_001", "template_key": "engineer-india", "template_version": "1"},
        {"hire_id": "../x", "template_key": "engineer-india", "template_version": 1},
    ],
)
def test_a_bad_body_is_422_and_starts_nothing(
    client: TestClient, memory_db: MemoryDatabase, body: dict[str, Any]
) -> None:
    _publish(client, _template())
    response = client.post("/runs", json=body, headers=_auth(HR))
    assert response.status_code == 422
    assert memory_db.raw(RUNS) == []


def test_a_failed_event_write_leaves_the_error_visible(client: TestClient, run_outbox: OutboxStandIn) -> None:
    """With the real bus, the change and the event are one transaction (tests_int proves the rollback);
    here the publish raising must surface as an error, never as a started run."""
    _publish(client, _template())
    run_outbox.fail_after_change = True
    response = _start(client)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_without_a_run_service_the_route_answers_503(users: Any, registry: Any) -> None:
    from onb_api import create_app
    from onb_api.testing import TEST_SETTINGS

    with TestClient(create_app(auth=TEST_SETTINGS, users=users, agents=registry)) as bare:
        response = bare.post(
            "/runs",
            json={"hire_id": "h_acme_001", "template_key": "engineer-india", "template_version": 1},
            headers=_auth(HR),
        )
    assert response.status_code == 503
