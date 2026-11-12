"""The admin views' data for the Playwright tests (UI-8), in memory, through the real services.

* the agent registry, synced from `config/agents/` as REG-1's command does;
* the LLM configuration, `config/llm.yaml` resolved with a fixed fake environment (no credentials);
* an audit log holding MCP hub denials for `db_agent` (HUB-1's record shape, ADR 0006), plus what the
  hire service itself audits;
* one hire created by HR through the hire service, and one reveal, so the PII access log has an entry;
* two eval runs in the shape the harness stores (`onb_api.testing_evals`; apps may not import the
  harness).

Test-only and synthetic: the hire's values come from PII-3's synthetic ranges, never a real person.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.testing import InMemoryRegistryStore
from onb_api.auth.tokens import Principal
from onb_api.documents.service import DocumentService
from onb_api.evals.store import EvalRunStore
from onb_api.hires.service import HireService
from onb_api.testing import InMemoryAuditLog, InMemoryUserStore
from onb_api.testing_evals import store_eval_run
from onb_api.testing_mongo import memory_keyring, memory_store
from onb_contracts.auth import Role
from onb_contracts.hires import NewHireRequest, PiiField
from onb_data import Actor, AuditOutcome, tenant_scope
from onb_llm import LLMConfig, load_llm_config
from run_seed import run_services

ROOT: Final = Path(__file__).resolve().parents[3]
ORG: Final = "org_acme"
T0: Final = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)

LLM_ENV: Final = {
    "LLM_CONNECTOR": "fake",
    "LLM_MODEL_FAST": "e2e-fast-model",
    "LLM_MODEL_STANDARD": "e2e-standard-model",
    "LLM_MODEL_DEEP": "e2e-deep-model",
}

# (tool target, denial reason) for db_agent, oldest first: what the hub records when it refuses a call.
DENIALS: Final = (
    ("tool:mongo.find/other", "collection_not_in_manifest"),
    ("tool:mongo.update_one/new_hires", "collection_not_in_manifest"),
    ("tool:mocks.provision_laptop", "server_not_in_manifest"),
)

HIRE: Final = {
    "name": "Meera Iyer",
    "role": "engineer",
    "location": "Pune",
    "start_date": "2026-12-14",
    "manager_id": "u_acme_mgr_ravi",
    "notes": "",
    "pii": {"email": "meera.iyer.e2e@example.com", "mobile": "+91 90000 00071", "pan": "AEEPI7101K"},
}

EVAL_RUNS: Final = (
    ("evr_20261102T090000Z_0e2e0001", "db_agent", True),
    ("evr_20261103T090000Z_0e2e0002", "run_assistant", False),
)


def llm_config() -> LLMConfig:
    return load_llm_config(ROOT / "config" / "llm.yaml", LLM_ENV)


async def registry() -> AgentRegistry:
    agents = AgentRegistry(InMemoryRegistryStore(), InMemoryAuditLog())
    manifests = load_manifests(ROOT / "config" / "agents")
    catalog = load_catalog(ROOT / "config" / "tool_catalog.yaml", manifests)
    await agents.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin"))
    return agents


async def audit_log() -> InMemoryAuditLog:
    log = InMemoryAuditLog()
    with tenant_scope(ORG):
        for minute, (target, reason) in enumerate(DENIALS):
            log.now = T0 + timedelta(minutes=minute)
            await log.audit(
                actor=Actor.agent("db_agent"),
                action="mcp.tool_call",
                target=target,
                outcome=AuditOutcome.DENIED,
                reason=reason,
            )
        log.now = T0 + timedelta(minutes=10)
        await log.audit(
            actor=Actor.agent("run_assistant"),
            action="mcp.tool_call",
            target="tool:mongo.find/run_tasks",
            outcome=AuditOutcome.SUCCESS,
        )
    log.now = None
    return log


async def hires(users: InMemoryUserStore, auditor: InMemoryAuditLog) -> tuple[HireService, DocumentService, str]:
    """The hire service, and (UI-4) the document service over the same store, keyring and audit log, as
    `onb_api.main` shares one keyring between them."""
    store = memory_store()
    keyring = memory_keyring(store)
    service = HireService.build(store, keyring, auditor=auditor, users=users)
    documents = DocumentService.build(store, keyring, auditor=auditor)
    await service.ensure_indexes()
    await documents.ensure_indexes()
    hr = Principal(user_id="u_acme_hr1", org_id=ORG, role=Role.HR)
    with tenant_scope(ORG):
        hire_id = (await service.create(hr, NewHireRequest.model_validate(HIRE))).id
        await service.reveal(hr, hire_id, PiiField.PAN, "payroll setup")
    return service, documents, hire_id


def _report(run_id: str, agent: str, passed: bool, started: datetime) -> dict[str, Any]:
    group = {
        "cases": 2,
        "repetitions": 6,
        "pass_at_1": 0.8333 if passed else 0.5,
        "pass_at_1_exact": "5/6" if passed else "1/2",
        "pass_hat_k": 0.5 if passed else 0.0,
        "pass_hat_k_exact": "1/2" if passed else "0",
        "tool_call_accuracy": 1.0,
        "avg_steps": 3.5,
        "p50_latency_ms": 12.0,
        "p95_latency_ms": 30.5,
        "avg_tokens": 410.0,
    }
    gate_detail = "50.0% >= 50.0%" if passed else "0.0% < 50.0%"
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
        "metrics": {agent: {"all": group}},
        "gates": [{"name": "pass_hat_k", "agent": agent, "passed": passed, "detail": gate_detail}],
        "cases": [
            {"case_id": f"{agent}-001", "agent": agent, "category": "happy", "repetitions": [{"passed": True}] * 3},
            {
                "case_id": f"{agent}-002",
                "agent": agent,
                "category": "happy",
                "repetitions": [{"passed": True}, {"passed": False}, {"passed": passed}],
            },
        ],
    }


async def eval_runs() -> EvalRunStore:
    store = memory_store()
    for day, (run_id, agent, passed) in enumerate(EVAL_RUNS):
        started = T0 + timedelta(days=day)
        await store_eval_run(store, ORG, _report(run_id, agent, passed, started))
    return EvalRunStore(store)


async def admin_services(users: InMemoryUserStore) -> dict[str, Any]:
    """The `create_app` arguments the admin views read, seeded (plus `run_driver`, UI-5: for the test router,
    not for `create_app`)."""
    log = await audit_log()
    hire_service, document_service, hire_id = await hires(users, log)
    agents = await registry()
    runs, driver = await run_services(agents, hire_service, log, hire_id)  # UI-5
    return {
        "audit": log,
        "agents": agents,
        "runs": runs,
        "run_driver": driver,
        "hires": hire_service,
        "documents": document_service,
        "evals": await eval_runs(),
        "llm": llm_config(),
    }
