"""API test fixtures: a client on a fresh app, with no telemetry left over from another test.

The app gets the test auth settings, an in-memory user store seeded with both orgs' users, an
empty in-memory audit log (`audit_log`), an in-memory agent registry synced from `config/agents/`
(REG-1), the hire service (HIRE-1) over an in-memory database holding seed_v1's hires, with its own
audit log (`hire_audit`), the template lifecycle (TPL-2) over an in-memory store, validating
against that registry and auditing to `audit_log`, the document service (HIRE-2) over the same
database, keyring (`keyring`, shared with the hires) and audit log as the hires, and the eval run
store (UI-8) over the same in-memory database. `JWT_SECRET` and `PII_MASTER_KEY` are set for every
test (fresh random values), so tests that build the app from the environment start like the real
service; the startup tests remove them.
"""

import asyncio
import base64
import json
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.testing import InMemoryRegistryStore
from onb_api import create_app
from onb_api.documents.service import DocumentService
from onb_api.evals.store import EvalRunStore
from onb_api.hires.service import HireService
from onb_api.templates import RegistryValidatorSource, TemplateLifecycle
from onb_api.notify.service import MockChannels
from onb_api.testing import (
    TEST_SETTINGS,
    WEBHOOK_SECRET,
    InMemoryAuditLog,
    InMemoryTemplateStore,
    InMemoryUserStore,
    RecordedDecisions,
    seeded_store,
)
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store
from onb_crypto import Keyring
from onb_data import Actor
from onb_telemetry.testing import reset_telemetry_globals

__all__ = [
    "app",
    "audit_log",
    "client",
    "decisions",
    "documents",
    "hire_audit",
    "hires",
    "jwt_secret_in_env",
    "keyring",
    "memory_db",
    "notify",
    "notify_db",
    "registry",
    "registry_retire",
    "template_store",
    "templates",
    "users",
]

CONFIG = Path(__file__).resolve().parents[3] / "config"
SEED = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "seed_v1.json"


@pytest.fixture(autouse=True)
def jwt_secret_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("PII_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secrets.token_urlsafe(32))


@pytest.fixture
def users() -> InMemoryUserStore:
    return asyncio.run(seeded_store())


@pytest.fixture
def registry() -> AgentRegistry:
    agents = AgentRegistry(InMemoryRegistryStore(), InMemoryAuditLog())
    manifests = load_manifests(CONFIG / "agents")
    catalog = load_catalog(CONFIG / "tool_catalog.yaml", manifests)
    asyncio.run(agents.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin")))
    return agents


@pytest.fixture
def registry_retire(registry: AgentRegistry) -> Callable[[str], None]:
    """Retire one agent in the registry, as a sync without its manifest does."""

    def retire(name: str) -> None:
        manifests = [m for m in load_manifests(CONFIG / "agents") if m.name != name]
        catalog = load_catalog(CONFIG / "tool_catalog.yaml", load_manifests(CONFIG / "agents"))
        asyncio.run(registry.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin")))

    return retire


@pytest.fixture
def memory_db() -> MemoryDatabase:
    """The in-memory database under the hire service, with seed_v1's hires of both orgs stored as
    seeded (their `ptk_seed_*` stand-ins resolve nowhere)."""
    db = MemoryDatabase()
    seed = json.loads(SEED.read_text())
    db["new_hires"].documents = [dict(doc) for doc in seed["new_hires"]]
    return db


@pytest.fixture
def hire_audit() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def keyring(memory_db: MemoryDatabase) -> Keyring:
    """One keyring (one master key) for the hire and document services, as `onb_api.main` builds them."""
    return memory_keyring(memory_store(memory_db))


@pytest.fixture
def hires(
    memory_db: MemoryDatabase, hire_audit: InMemoryAuditLog, users: InMemoryUserStore, keyring: Keyring
) -> HireService:
    return HireService.build(memory_store(memory_db), keyring, auditor=hire_audit, users=users)


@pytest.fixture
def template_store() -> InMemoryTemplateStore:
    return InMemoryTemplateStore()


@pytest.fixture
def templates(
    template_store: InMemoryTemplateStore, registry: AgentRegistry, audit_log: InMemoryAuditLog
) -> TemplateLifecycle:
    return TemplateLifecycle(template_store, RegistryValidatorSource.from_config(registry, CONFIG), audit_log)


@pytest.fixture
def documents(memory_db: MemoryDatabase, hire_audit: InMemoryAuditLog, keyring: Keyring) -> DocumentService:
    """HIRE-2: document uploads over the same in-memory database, keyring and audit log as the hires."""
    return DocumentService.build(memory_store(memory_db), keyring, auditor=hire_audit)


@pytest.fixture
def notify_db() -> MemoryDatabase:
    """NOTIF-1: the in-memory database under the mock channels (`mock_outbox`, `telegram_callbacks`)."""
    return MemoryDatabase()


@pytest.fixture
def decisions() -> RecordedDecisions:
    return RecordedDecisions()


@pytest.fixture
def notify(notify_db: MemoryDatabase, decisions: RecordedDecisions) -> MockChannels:
    return MockChannels(memory_store(notify_db), webhook_secret=WEBHOOK_SECRET, decisions=decisions)


@pytest.fixture
def app(
    users: InMemoryUserStore,
    registry: AgentRegistry,
    audit_log: InMemoryAuditLog,
    hires: HireService,
    templates: TemplateLifecycle,
    documents: DocumentService,
    memory_db: MemoryDatabase,
    notify: MockChannels,
) -> FastAPI:
    reset_telemetry_globals()
    return create_app(
        auth=TEST_SETTINGS,
        users=users,
        audit=audit_log,
        agents=registry,
        hires=hires,
        templates=templates,
        documents=documents,
        evals=EvalRunStore(memory_store(memory_db)),
        notify=notify,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions stays on (the default): an unhandled error must become a response,
    # never escape the app.
    with TestClient(app) as test_client:
        yield test_client
