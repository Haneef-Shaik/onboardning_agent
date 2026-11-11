"""`GET /evals/runs`, `GET /evals/runs/{run_id}`: HR_ADMIN reads the org's eval runs (UI-8; PRD §9, §10 Evals).

The records are written by the harness's own writer (`onb_evals.results.record_run`), so the read
model is tested against what the harness stores, not against a hand-made copy of it. HR and MANAGER
get 403 before the store is read; another org's run answers exactly like a run that does not exist.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.evals.store import EVAL_RUNS_COLLECTION, EvalRunStore
from onb_api.testing import TEST_SETTINGS, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.auth import Role
from onb_contracts.evals import EVAL_RUN_ID_PATTERN
from onb_evals.results import EVAL_RUNS_COLLECTION as HARNESS_COLLECTION
from onb_evals.results import record_run
from onb_evals.session import _run_id

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
GLOBEX_ADMIN = ("u_globex_admin", "org_globex", Role.HR_ADMIN)

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
ACME_OLD = "evr_20261102T090000Z_0000aaa1"
ACME_NEW = "evr_20261103T090000Z_0000aaa2"
GLOBEX_RUN = "evr_20261104T090000Z_0000bbb1"
MISSING_RUN = "evr_20261105T090000Z_0000ccc1"


def report(run_id: str, started: datetime, *, agent: str = "db_agent", passed: bool = True) -> dict[str, Any]:
    """A report as `onb_evals.report.build_report` shapes it (only the fields `record_run` stores)."""
    group = {
        "cases": 2,
        "repetitions": 6,
        "pass_at_1": 0.8333,
        "pass_at_1_exact": "5/6",
        "pass_hat_k": 0.5,
        "pass_hat_k_exact": "1/2",
        "tool_call_accuracy": 1.0,
        "avg_steps": 3.5,
        "p50_latency_ms": 12.0,
        "p95_latency_ms": 30.5,
        "avg_tokens": 410.0,
    }
    return {
        "run_id": run_id,
        "mode": "full",
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(minutes=4)).isoformat(),
        "agent": agent,
        "llm": "standard",
        "dataset": "onboarding_agents_v1.jsonl",
        "fixture": "seed_v1",
        "repeat": 3,
        "passed": passed,
        "all_cases_passed": False,
        "metrics": {agent: {"all": group, "happy": group}},
        "gates": [{"name": "pass_hat_k", "agent": agent, "passed": passed, "detail": "50.0% >= 50.0%"}],
        "cases": [
            {"case_id": "db-001", "agent": agent, "category": "happy", "repetitions": [{"passed": True}] * 3},
            {
                "case_id": "db-002",
                "agent": agent,
                "category": "happy",
                "repetitions": [{"passed": True}, {"passed": False}, {"passed": True}],
            },
        ],
    }


async def _seed(db: MemoryDatabase) -> None:
    store = memory_store(db)
    await record_run(store, "org_acme", report(ACME_OLD, T0))
    await record_run(store, "org_acme", report(ACME_NEW, T0 + timedelta(days=1), agent="run_assistant", passed=False))
    await record_run(store, "org_globex", report(GLOBEX_RUN, T0 + timedelta(days=2), agent="globex_only_agent"))


@pytest.fixture
def seeded(memory_db: MemoryDatabase) -> MemoryDatabase:
    asyncio.run(_seed(memory_db))
    return memory_db


def _get(client: TestClient, path: str, actor: tuple[str, str, Role] = ADMIN, **params: Any) -> Any:
    return client.get(path, params=params, headers=bearer(token_for(*actor)))


# --- reading -------------------------------------------------------------------------------------------


def test_hr_admin_lists_the_orgs_runs_newest_first(seeded: MemoryDatabase, client: TestClient) -> None:
    body = _get(client, "/evals/runs").json()
    assert [run["id"] for run in body["data"]] == [ACME_NEW, ACME_OLD]
    assert body["meta"]["page"] == {"total": 2, "page": 1, "limit": 20}
    newest = body["data"][0]
    assert newest == {
        "id": ACME_NEW,
        "mode": "full",
        "agent": "run_assistant",
        "llm": "standard",
        "dataset": "onboarding_agents_v1.jsonl",
        "fixture": "seed_v1",
        "repeat": 3,
        "passed": False,
        "all_cases_passed": False,
        "started_at": "2026-11-03T09:00:00Z",
        "finished_at": "2026-11-03T09:04:00Z",
    }


def test_the_list_filters_by_agent_and_pages(seeded: MemoryDatabase, client: TestClient) -> None:
    assert [r["id"] for r in _get(client, "/evals/runs", agent="db_agent").json()["data"]] == [ACME_OLD]
    second = _get(client, "/evals/runs", page=2, limit=1).json()
    assert [r["id"] for r in second["data"]] == [ACME_OLD]
    assert second["meta"]["page"] == {"total": 2, "page": 2, "limit": 1}


def test_a_run_shows_its_metrics_gates_and_cases(seeded: MemoryDatabase, client: TestClient) -> None:
    data = _get(client, f"/evals/runs/{ACME_OLD}").json()["data"]
    assert data["id"] == ACME_OLD
    assert data["metrics"]["db_agent"]["all"] == {
        "cases": 2,
        "repetitions": 6,
        "pass_at_1": 0.8333,
        "pass_hat_k": 0.5,
        "tool_call_accuracy": 1.0,
        "avg_steps": 3.5,
        "p50_latency_ms": 12.0,
        "p95_latency_ms": 30.5,
        "avg_tokens": 410.0,
    }
    assert data["gates"] == [{"name": "pass_hat_k", "agent": "db_agent", "passed": True, "detail": "50.0% >= 50.0%"}]
    assert data["cases"] == [
        {"case_id": "db-001", "agent": "db_agent", "category": "happy", "passes": 3, "repetitions": 3},
        {"case_id": "db-002", "agent": "db_agent", "category": "happy", "passes": 2, "repetitions": 3},
    ]


def test_another_orgs_run_is_not_found_like_a_missing_one(seeded: MemoryDatabase, client: TestClient) -> None:
    foreign = _get(client, f"/evals/runs/{GLOBEX_RUN}")
    missing = _get(client, f"/evals/runs/{MISSING_RUN}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert GLOBEX_RUN not in _get(client, "/evals/runs").text
    # And symmetrically: org B's admin sees only org B's run.
    assert [r["id"] for r in _get(client, "/evals/runs", GLOBEX_ADMIN).json()["data"]] == [GLOBEX_RUN]
    assert _get(client, f"/evals/runs/{ACME_OLD}", GLOBEX_ADMIN).status_code == 404


def test_naming_another_org_in_the_query_changes_nothing(seeded: MemoryDatabase, client: TestClient) -> None:
    plain = _get(client, "/evals/runs").json()["data"]
    steered = _get(client, "/evals/runs", org_id="org_globex", orgId="org_globex", tenant="org_globex").json()["data"]
    assert steered == plain


# --- box 2: HR and MANAGER get 403 ----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/evals/runs", f"/evals/runs/{ACME_OLD}"])
@pytest.mark.parametrize(("actor", "status"), [(ADMIN, 200), (HR, 403), (MANAGER, 403)], ids=["admin", "hr", "mgr"])
def test_eval_runs_are_hr_admin_only(
    seeded: MemoryDatabase, client: TestClient, path: str, actor: tuple[str, str, Role], status: int
) -> None:
    response = _get(client, path, actor)
    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert response.json()["data"] is None


@pytest.mark.parametrize("path", ["/evals/runs", f"/evals/runs/{ACME_OLD}"])
def test_eval_runs_need_a_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_hr_and_managers_are_refused_before_the_store_is_read(app: FastAPI, client: TestClient) -> None:
    calls: list[str] = []

    class Spy:
        async def page(self, *_: Any, **__: Any) -> Any:
            calls.append("page")
            raise AssertionError("read")

        async def get(self, *_: Any, **__: Any) -> Any:
            calls.append("get")
            raise AssertionError("read")

    app.state.evals = Spy()
    for actor in (HR, MANAGER):
        assert _get(client, "/evals/runs", actor).status_code == 403
        assert _get(client, f"/evals/runs/{ACME_OLD}", actor).status_code == 403
    assert calls == []


# --- input bounds and wiring ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/evals/runs/not-a-run-id", {}),
        ("/evals/runs/evr_20261102T090000Z_0000AAA1", {}),  # upper-case hex
        ("/evals/runs", {"agent": "Not An Agent"}),
        ("/evals/runs", {"limit": 101}),
        ("/evals/runs", {"page": 0}),
        ("/evals/runs", {"page": 102, "limit": 100}),  # deeper than MAX_EVAL_RUN_SKIP
    ],
)
def test_out_of_bounds_input_is_422(client: TestClient, path: str, params: dict[str, Any]) -> None:
    response = _get(client, path, **params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_without_an_eval_store_the_routes_answer_503() -> None:
    app = create_app(auth=TEST_SETTINGS)
    with TestClient(app) as client:
        for path in ("/evals/runs", f"/evals/runs/{ACME_OLD}"):
            response = _get(client, path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_startup_makes_the_org_prefixed_eval_runs_index() -> None:
    db = MemoryDatabase()
    app = create_app(auth=TEST_SETTINGS, evals=EvalRunStore(memory_store(db)))
    with TestClient(app):
        pass
    assert ("org_id", "started_at", "_id") in [index.keys for index in db["eval_runs"].indexes]


def test_the_api_reads_the_collection_the_harness_writes() -> None:
    assert EVAL_RUNS_COLLECTION == HARNESS_COLLECTION


def test_the_run_id_pattern_matches_the_harness_ids() -> None:
    for moment in (T0, datetime(2027, 1, 31, 23, 59, 59, tzinfo=UTC)):
        assert re.fullmatch(EVAL_RUN_ID_PATTERN, _run_id(moment))


def test_a_malformed_stored_run_is_an_internal_error_not_a_partial_answer(
    memory_db: MemoryDatabase, client: TestClient
) -> None:
    memory_db["eval_runs"].documents.append({"_id": ACME_OLD, "org_id": "org_acme", "mode": "full"})
    for path in ("/evals/runs", f"/evals/runs/{ACME_OLD}"):
        response = _get(client, path)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
