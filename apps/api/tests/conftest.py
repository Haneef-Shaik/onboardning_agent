"""API test fixtures: a client on a fresh app, with no telemetry left over from another test.

The app gets the test auth settings, an in-memory user store seeded with both orgs' users, an
empty in-memory audit log, an in-memory agent registry synced from `config/agents/` (REG-1), and
the hire service (HIRE-1) over an in-memory database holding seed_v1's hires, with its own audit log
(`hire_audit`). `JWT_SECRET` and `PII_MASTER_KEY` are set for every test (fresh random values), so
tests that build the app from the environment start like the real service; the startup tests
remove them.
"""

import asyncio
import base64
import json
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.testing import InMemoryRegistryStore
from onb_api import create_app
from onb_api.hires.service import HireService
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, InMemoryUserStore, seeded_store
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store
from onb_data import Actor
from onb_telemetry.testing import reset_telemetry_globals

__all__ = ["app", "client", "hire_audit", "hires", "jwt_secret_in_env", "memory_db", "registry", "users"]

CONFIG = Path(__file__).resolve().parents[3] / "config"
SEED = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "seed_v1.json"


@pytest.fixture(autouse=True)
def jwt_secret_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("PII_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode("ascii"))


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
def hires(memory_db: MemoryDatabase, hire_audit: InMemoryAuditLog, users: InMemoryUserStore) -> HireService:
    store = memory_store(memory_db)
    return HireService.build(store, memory_keyring(store), auditor=hire_audit, users=users)


@pytest.fixture
def app(users: InMemoryUserStore, registry: AgentRegistry, hires: HireService) -> FastAPI:
    reset_telemetry_globals()
    return create_app(auth=TEST_SETTINGS, users=users, audit=InMemoryAuditLog(), agents=registry, hires=hires)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions stays on (the default): an unhandled error must become a response,
    # never escape the app.
    with TestClient(app) as test_client:
        yield test_client
