"""The cross-tenant suite (`make test-xt`, PRD §7.9 Testing) on the compose MongoDB replica set.

Every test gets a freshly seeded two-org world (see `world.py`) in one scratch database, dropped at
the end of the session, and runs under the T-2 tenant safety net. Root read-backs across orgs say why
in `unscoped("reason")`.

Mongo publishes no port (PRD §7.11), so a short-lived loopback tunnel (a socat container on the
stack's network, as in `apps/api/tests_int/conftest.py`) carries the root login for setup. The
application objects under test (the API, mcp-mongo, the event bus) reach it through `onb_data`'s
`TenantRepository` like they do in production.

The end of the run prints the surface the suite covered: every route, MCP tool and event consumer.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.registry.mongo import ORGS, MongoRegistryStore
from onb_api import create_app
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.passwords import hash_password
from onb_api.auth.tokens import Principal
from onb_api.auth.users import NewUser
from onb_api.documents.service import DocumentService
from onb_api.evals.store import EvalRunStore
from onb_api.hires.service import HireService
from onb_api.notify.service import BusDecisions, MockChannels
from onb_api.templates import MongoTemplateStore, RegistryValidatorSource, TemplateLifecycle
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog
from onb_contracts.auth import Role
from onb_contracts.hires import NewHireRequest, PiiField
from onb_contracts.templates import Template
from onb_crypto import ORG_KEYS_COLLECTION, Keyring, MasterKey, RepositoryOrgKeyStore, SecretKey
from onb_data import Actor, ActorType, AuditLog, Auditor, AuditOutcome, DataStore, connect, tenant_scope
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)
from onb_evals.results import record_run
from onb_mcp_mocks.notify.main import build_service as build_notify_service
from onb_mcp_mocks.notify.server import AUDIENCE as NOTIFY_AUDIENCE
from onb_mcp_mocks.server import AUDIENCE as MOCKS_AUDIENCE
from onb_mcp_mocks.server import build_server as build_mocks_server
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mongo.call_context import AUDIENCE as MONGO_AUDIENCE
from onb_mcp_mongo.server import build_server
from pymongo import MongoClient
from pymongo.database import Database

from . import world
from .surface import PLATFORM_SUBSCRIPTIONS, api_routes, mcp_tools, notify_settings, platform_consumers

# Re-exported so pytest registers the safety net hooks and fixtures for this suite.
__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]

# Pinned by digest, as in the other suites: this container carries the root login across the tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
# Its own label key, so another suite's stale-tunnel cleanup never removes this one.
TUNNEL_LABEL_KEY = "onb.test.xt-mongo-tunnel"
MCP_CONTEXT_KEY = secrets.token_urlsafe(48)
MOCKS_CONTEXT_KEY = secrets.token_urlsafe(48)  # its own key, as deployed
NOTIFY_CONTEXT_KEY = secrets.token_urlsafe(48)  # NOTIF-1: mcp-notify's own key
XT_WEBHOOK_SECRET = secrets.token_urlsafe(32)  # NOTIF-1: the Telegram webhook secret token
PASSWORD = secrets.token_urlsafe(12)
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
# HIRE-1: the vault's master key for this run (random, never a fixed secret).
XT_MASTER_KEY = MasterKey(SecretKey.generate())
# The fields every seeded vault hire shares; `world.XT_VAULT_HIRES` gives the rest.
XT_HIRE_DEFAULTS: Mapping[str, Any] = {
    "role": "engineer",
    "location": "Pune",
    "start_date": "2026-12-14",
    "notes": "",
}

RootClient = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=world.ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run the suite with `make test-xt` (it loads .env)")
    return value


@pytest.fixture(scope="session")
def mongo_tunnel() -> Iterator[str]:
    container = _run("docker", "compose", "ps", "-q", "mongo")
    if not container:
        pytest.fail("the compose mongo service is not running: run `make up`")
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{end}}"
    network, mongo_ip = _run("docker", "inspect", "-f", fmt, container).split()
    label = f"{TUNNEL_LABEL_KEY}={network}"
    stale = _run("docker", "ps", "-aq", "--filter", f"label={label}").split()
    if stale:
        subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, check=False)
    tunnel = _run(
        "docker", "run", "-d", "--rm", "--label", label, "-p", "127.0.0.1::27017",
        SOCAT_IMAGE, "TCP-LISTEN:27017,fork,reuseaddr", f"TCP:{mongo_ip}:27017",
    )  # fmt: skip
    try:
        _run("docker", "network", "connect", network, tunnel)
        yield _run("docker", "port", tunnel, "27017/tcp").splitlines()[0]
    finally:
        subprocess.run(["docker", "rm", "-f", tunnel], capture_output=True, check=False)


@pytest.fixture(scope="session")
def mongo_uri(mongo_tunnel: str) -> str:
    user = quote_plus(_required_env("MONGO_ROOT_USERNAME"))
    password = quote_plus(_required_env("MONGO_ROOT_PASSWORD"))
    return f"mongodb://{user}:{password}@{mongo_tunnel}/?authSource=admin&directConnection=true"


@pytest.fixture(scope="session")
def root_client(mongo_uri: str) -> Iterator[RootClient]:
    client: RootClient = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def database_name(root_client: RootClient) -> Iterator[str]:
    name = f"xt_{uuid.uuid4().hex[:12]}"
    try:
        yield name
    finally:
        root_client.drop_database(name)


# --- the two-org world ------------------------------------------------------------------------------


@dataclass(frozen=True)
class World:
    """The seeded scratch database. `root_db` is for read-backs across orgs, inside `unscoped`."""

    uri: str = field(repr=False)  # carries the root password: never in a repr or assertion message
    database: str
    root_db: Database[dict[str, Any]] = field(repr=False)
    password: str = field(repr=False)
    _vault_hires: dict[str, str] = field(default_factory=dict, repr=False)

    def vault_hires(self) -> Mapping[str, str]:
        """HIRE-1: the id of each org's vault-backed hire (`world.XT_VAULT_HIRES`), by org. Seeded on
        first use only, so the suites that count `new_hires` against the seed see the seed alone."""
        if not self._vault_hires:

            async def main() -> dict[str, str]:
                store = self.store()
                try:
                    return await _seed_hires(store)
                finally:
                    await store.close()

            self._vault_hires.update(asyncio.run(main()))
        return self._vault_hires

    def store(self) -> DataStore:
        return connect(self.uri, self.database, app_name="xt")

    def org_b_snapshot(self, unscoped: Unscoped) -> dict[str, list[dict[str, Any]]]:
        """Every org-B document in every collection, read as root: the before/after of a case."""
        with unscoped("root snapshot of org B's documents, to prove an org-A call changed none of them"):
            snapshot = {
                name: sorted(self.root_db[name].find({"org_id": world.ORG_B}), key=repr)
                for name in sorted(self.root_db.list_collection_names())
            }
        # A collection org A's call created holds no org-B document: only non-empty ones compare.
        return {name: docs for name, docs in snapshot.items() if docs}


async def _seed(store: DataStore) -> None:
    users = MongoUserStore(store)
    await users.ensure_indexes()
    password_hash = hash_password(PASSWORD, rounds=TEST_SETTINGS.bcrypt_rounds)
    for user in world.seed_users():
        with tenant_scope(user["org_id"]):
            new = NewUser(
                email=world.email_of(user["_id"]),
                name=user["name"],
                role=Role(user["role"]),
                password_hash=password_hash,
            )
            await users.add_user(new, user_id=user["_id"])
    for org_id in (world.ORG_A, world.ORG_B):
        with tenant_scope(org_id):
            for collection, docs in world.tenant_documents(org_id).items():
                if docs:
                    await store.tenant(collection).insert_many(docs)
    await _seed_registry(store)
    await _seed_templates(store)
    minutes = iter(range(len(world.AUDIT_ROWS)))
    log = AuditLog(store, clock=lambda: T0 + timedelta(minutes=next(minutes)))
    for org_id, actor_type, actor_id, action, target, outcome in world.AUDIT_ROWS:
        actor = Actor.user(actor_id) if actor_type == ActorType.USER else Actor.agent(actor_id)
        with tenant_scope(org_id):
            await log.audit(actor=actor, action=action, target=target, outcome=AuditOutcome(outcome), reason="xt")
    await _seed_eval_runs(store)


def template_lifecycle(store: DataStore, audit: Auditor, registry: AgentRegistry, **options: Any) -> TemplateLifecycle:
    validators = RegistryValidatorSource.from_config(registry, world.ROOT / "config")
    return TemplateLifecycle(MongoTemplateStore(store), validators, audit, **options)


async def _seed_templates(store: DataStore) -> None:
    """Each org's drafts and versions (`world.template_drafts`), through the lifecycle itself. Its audit
    records go to an in-memory log, so the seeded audit totals stay exactly `world.AUDIT_ROWS`."""
    registry = AgentRegistry(MongoRegistryStore(store), InMemoryAuditLog())
    for org_id in (world.ORG_A, world.ORG_B):
        drafts = world.template_drafts(org_id)
        ids = iter([draft_id for draft_id, _, _ in drafts])
        lifecycle = template_lifecycle(store, InMemoryAuditLog(), registry, new_draft_id=lambda ids=ids: next(ids))
        await lifecycle.ensure_indexes()
        with tenant_scope(org_id):
            for draft_id, template, publish in drafts:
                await lifecycle.create_draft(Template.model_validate(template), actor_id="u_xt_seed")
                if publish:
                    await lifecycle.publish(draft_id, actor_id="u_xt_seed")


def eval_report(run_id: str, agent: str, started: datetime) -> dict[str, Any]:
    """UI-8: a harness report with the fields `record_run` stores; the agent's name runs through it."""
    group = {
        "cases": 1,
        "repetitions": 3,
        "pass_at_1": 1.0,
        "pass_at_1_exact": "1",
        "pass_hat_k": 1.0,
        "pass_hat_k_exact": "1",
        "tool_call_accuracy": 1.0,
        "avg_steps": 2.0,
        "p50_latency_ms": 10.0,
        "p95_latency_ms": 12.0,
        "avg_tokens": 100.0,
    }
    return {
        "run_id": run_id,
        "mode": "full",
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(minutes=3)).isoformat(),
        "agent": agent,
        "llm": "standard",
        "dataset": "onboarding_agents_v1.jsonl",
        "fixture": "seed_v1",
        "repeat": 3,
        "passed": True,
        "all_cases_passed": True,
        "metrics": {agent: {"all": group}},
        "gates": [{"name": "pass_hat_k", "agent": agent, "passed": True, "detail": "100.0% >= 50.0%"}],
        "cases": [
            {"case_id": f"{agent}-001", "agent": agent, "category": "happy", "repetitions": [{"passed": True}] * 3}
        ],
    }


async def _seed_eval_runs(store: DataStore) -> None:
    """UI-8: each org's eval runs (`world.EVAL_RUNS`), through the harness's own writer."""
    for minutes, (org_id, run_id, agent) in enumerate(world.EVAL_RUNS):
        await record_run(store, org_id, eval_report(run_id, agent, T0 + timedelta(minutes=minutes)))


async def _seed_hires(store: DataStore) -> dict[str, str]:
    """One vault-backed hire per org (`world.XT_VAULT_HIRES`), created by HR through the hire service,
    and one reveal in org B so its access log holds an entry. Their audit records go to an in-memory
    log, so the seeded audit totals per org stay exactly `world.AUDIT_ROWS`."""
    service = HireService.build(store, xt_keyring(store), auditor=InMemoryAuditLog(), users=MongoUserStore(store))
    await service.ensure_indexes()
    hr = {world.ORG_A: "u_acme_hr1", world.ORG_B: "u_globex_hr1"}
    ids: dict[str, str] = {}
    for org_id, hire in world.XT_VAULT_HIRES.items():
        principal = Principal(user_id=hr[org_id], org_id=org_id, role=Role.HR)
        request = NewHireRequest.model_validate({**XT_HIRE_DEFAULTS, **hire})
        with tenant_scope(org_id):
            ids[org_id] = (await service.create(principal, request)).id
    globex = Principal(user_id=hr[world.ORG_B], org_id=world.ORG_B, role=Role.HR)
    with tenant_scope(world.ORG_B):
        await service.reveal(globex, ids[world.ORG_B], PiiField.EMAIL, "xt seed")
    return ids


async def _seed_registry(store: DataStore) -> None:
    """The global `orgs` and the agent registry, synced from `config/` as REG-1's command does.

    The sync audits each change in every org; those records go to an in-memory log here, so the
    seeded audit totals per org stay exactly `world.AUDIT_ROWS`.
    """
    await store.shared(ORGS).insert_many([{"_id": org["_id"], "name": org["name"]} for org in world.seed()["orgs"]])
    manifests = load_manifests(world.ROOT / "config" / "agents")
    catalog = load_catalog(world.ROOT / "config" / "tool_catalog.yaml", manifests)
    registry = AgentRegistry(MongoRegistryStore(store), InMemoryAuditLog())
    await registry.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin"))


@pytest.fixture
def xt_world(root_client: RootClient, mongo_uri: str, database_name: str) -> World:
    """A fresh copy of the two-org world for this test."""
    root_client.drop_database(database_name)
    seeded = World(mongo_uri, database_name, root_client[database_name], PASSWORD)

    async def main() -> None:
        store = seeded.store()
        try:
            await _seed(store)
        finally:
            await store.close()

    asyncio.run(main())
    return seeded


# --- the API ----------------------------------------------------------------------------------------


def build_api(store: DataStore) -> FastAPI:
    """The app as `onb_api.main` builds it, over the scratch database. A new `create_app` argument
    gets its Mongo-backed value here."""
    audit = AuditLog(store)
    agents = AgentRegistry(MongoRegistryStore(store), audit)
    users = MongoUserStore(store)
    keyring = xt_keyring(store)
    hires = HireService.build(store, keyring, auditor=audit, users=users)
    templates = template_lifecycle(store, audit, agents)
    documents = DocumentService.build(store, keyring, auditor=audit)
    evals = EvalRunStore(store)
    notify = MockChannels(store, webhook_secret=XT_WEBHOOK_SECRET, decisions=BusDecisions(store))
    return create_app(
        auth=TEST_SETTINGS,
        users=users,
        audit=audit,
        agents=agents,
        hires=hires,
        templates=templates,
        documents=documents,
        evals=evals,
        notify=notify,
    )


def xt_keyring(store: DataStore) -> Keyring:
    """The keyring over the scratch database's `org_keys`, under this session's master key."""
    return Keyring(XT_MASTER_KEY, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))


@pytest.fixture
def api(xt_world: World) -> Iterator[TestClient]:
    with TestClient(build_api(xt_world.store())) as client:
        yield client


# --- mcp-mongo, served over HTTP on loopback ----------------------------------------------------------


@dataclass(frozen=True)
class McpServer:
    url: str
    key: str = field(repr=False)
    audience: str = MONGO_AUDIENCE


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _serve(app: Any, port: int, name: str) -> Iterator[None]:
    uv_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 15
    while not uv_server.started:
        if time.monotonic() > end or not thread.is_alive():
            pytest.fail(f"{name} did not start")
        time.sleep(0.05)
    try:
        yield
    finally:
        uv_server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def mcp_mongo(mongo_uri: str, database_name: str) -> Iterator[McpServer]:
    port = _free_port()
    server = build_server(connect(mongo_uri, database_name, app_name="xt-mcp-mongo"), MCP_CONTEXT_KEY, port=port)
    for _ in _serve(server.streamable_http_app(), port, "mcp-mongo"):
        yield McpServer(f"http://127.0.0.1:{port}/mcp", MCP_CONTEXT_KEY)


@pytest.fixture(scope="session")
def mcp_mocks() -> Iterator[McpServer]:
    port = _free_port()
    server = build_mocks_server(InMemoryIdempotencyStore(), MOCKS_CONTEXT_KEY, port=port)
    for _ in _serve(server.streamable_http_app(), port, "mcp-mocks"):
        yield McpServer(f"http://127.0.0.1:{port}/mcp", MOCKS_CONTEXT_KEY, MOCKS_AUDIENCE)


@pytest.fixture(scope="session")
def mcp_notify(mongo_uri: str, database_name: str) -> Iterator[McpServer]:
    """NOTIF-1: the notify sink over the scratch database, with this run's vault master key (so it can
    resolve the seeded vault hires) and grant key (`surface.XT_GRANT_KEY`), and the real bus."""
    port = _free_port()
    store = connect(mongo_uri, database_name, app_name="xt-mcp-notify")
    server = build_notify_service(notify_settings(NOTIFY_CONTEXT_KEY, XT_MASTER_KEY), store).server
    for _ in _serve(server.streamable_http_app(), port, "mcp-notify"):
        yield McpServer(f"http://127.0.0.1:{port}/mcp", NOTIFY_CONTEXT_KEY, NOTIFY_AUDIENCE)


# --- the covered surface, printed at the end of the run --------------------------------------------------


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Box 1: the run lists every route (and tool and consumer) it covered."""
    write = terminalreporter.write_line
    offline = connect("mongodb://127.0.0.1:1", "xt_listing")  # lazy: never connects
    try:
        routes = api_routes(build_api(offline))
        tools = mcp_tools(offline, MCP_CONTEXT_KEY)
        consumers = platform_consumers(PLATFORM_SUBSCRIPTIONS)
    except Exception as exc:  # never hide the run's own failures behind the listing's
        write(f"cross-tenant surface: could not be listed ({type(exc).__name__})", red=True)
        return
    write(f"cross-tenant surface: {len(routes)} API routes, {len(tools)} MCP tools, {len(consumers)} event consumers")
    for method, path in routes:
        write(f"  route    {method:6} {path}")
    for server, tool in tools:
        write(f"  tool     {server}.{tool}")
    for consumer in consumers:
        write(f"  consumer {consumer}")
