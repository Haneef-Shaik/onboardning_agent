"""The real stack for the hub's integration and cross-tenant tests (PRD §7.4, §7.5, §7.9):

- a throw-away MongoDB (testcontainers, the compose image) with two orgs, `org_acme` and `org_globex`;
- the MCP database users provisioned by mcp-mongo's own `db_users.provision` (MCP-3);
- mcp-mongo served by uvicorn on loopback over those users (`build_server(..., write_store=...)`);
- mcp-mocks served by uvicorn on loopback over an in-memory idempotency store, with its own key (MOCK-1);
- mcp-notify (NOTIF-1) served by uvicorn on loopback over this MongoDB: the real vault, keyring and PII-4
  resolver; the orchestrator's grant key is generated per session. This container is no replica set,
  so its events are written directly (`DirectEvents`); the bus transaction is tested in mcp-mocks' own
  tests_int against the compose replica set;
- the agent registry holding the six config/agents manifests plus `xt_agent` (every Mongo tool);
- the hub served by uvicorn on loopback: registry and `AuditLog` over its own client, catalog = every
  platform tool, downstream = that mcp-mongo and mcp-mocks, credentials = one per-session key each.

Both servers run in this process, so one in-memory exporter sees every span they produce. Secrets
are generated per session and never written anywhere.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import uvicorn
from onb_agents.registry import AgentRegistry, load_manifests, parse_manifest
from onb_agents.registry.mongo import MongoRegistryStore
from onb_api.testing_mongo import seed_vault_value
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    GrantAudit,
    GrantIssuer,
    GrantSigningKey,
    GrantVerifier,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    SecretKey,
    SinkResolver,
    VaultField,
    ensure_grant_indexes,
    load_sink_policy,
)
from onb_data import AuditLog, DataStore, connect, tenant_scope
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)
from onb_mcp_hub.catalog import MOCKS_SERVER, NOTIFY_SERVER, platform_catalog
from onb_mcp_hub.downstream import McpDownstream
from onb_mcp_hub.gateway import Gateway
from onb_mcp_hub.server import build_hub
from onb_mcp_mocks.notify.outbox import TelegramMockChannel, ensure_outbox_indexes
from onb_mcp_mocks.notify.sends import ensure_send_indexes
from onb_mcp_mocks.notify.server import build_notify_server
from onb_mcp_mocks.notify.sink import NotifySink
from onb_mcp_mocks.server import build_server as build_mocks_server
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_mcp_mongo.db_users import provision
from onb_mcp_mongo.server import build_server
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from testcontainers.community.mongodb import MongoDbContainer

__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]

# Same image and digest as infra/docker-compose.yml.
MONGO_IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
DATABASE = "onb_hub_int"
ORGS = ("org_acme", "org_globex")
ROOT = Path(__file__).resolve().parents[3]
RootClient = MongoClient[dict[str, Any]]

# A test agent granted every hub tool (every Mongo tool, every mock and notify), so each has an allowed path.
XT_AGENT = {
    "name": "xt_agent",
    "purpose": "Cross-tenant and integration tests of every hub tool",
    "llm": {"alias": "standard"},
    "mcp": {
        "mongo": {
            "tools": ["list_collections", "find", "count", "aggregate", "insert_one", "update_one"],
            "collections": {"read": ["run_tasks", "new_hires"], "write": ["run_tasks"]},
        },
        "mocks": {"tools": ["create_bitbucket_account", "provision_laptop"]},
        "notify": {"tools": ["notify_send"]},
    },
    "limits": {"max_steps": 8, "max_tokens": 1000, "timeout_s": 60},
}


@dataclass(frozen=True)
class Notify:
    """The notify sink behind the hub: who signs grants, and the vault tokens seeded per org."""

    issuer: GrantIssuer
    tokens: dict[str, str]  # org -> that org's hire mobile token
    hires: dict[str, str]  # org -> hire id
    events: list[str]

    def grant(self, org: str, *, run_id: str = "run_xt", task_id: str = "welcome_message") -> str:
        with tenant_scope(org):
            return self.issuer.issue_grant(
                org,
                run_id=run_id,
                task_id=task_id,
                subject_id=self.hires[org],
                fields=[VaultField.MOBILE],
                sink="notify.telegram",
            )


class DirectEvents:
    """`NotifyEvents` for a standalone MongoDB (no transactions): the change, then a record of the event."""

    def __init__(self, seen: list[str]) -> None:
        self._seen = seen

    async def sent(self, payload: Mapping[str, Any], *, key: str, run_id: str | None, change: Any) -> None:
        await change()
        self._seen.append("notification.sent")

    async def rejected(self, payload: Mapping[str, Any], *, run_id: str) -> None:
        self._seen.append("task.sink_rejected")


@dataclass(frozen=True)
class Stack:
    hub_url: str
    token_key: str
    context_key: str
    mongo_uri: str
    telemetry: CapturedTelemetry
    mocks_store: InMemoryIdempotencyStore
    mocks_key: str
    notify: Notify


def _wait_for_ping(client: RootClient, deadline_s: float = 180) -> None:
    """The image's init step starts a temporary mongod first; wait for the real one to answer.
    Generous: under `make test-int` several suites start containers on one Docker host at once."""
    end = time.monotonic() + deadline_s
    while True:
        try:
            client.admin.command("ping")
            return
        except PyMongoError:
            if time.monotonic() > end:
                raise
            time.sleep(0.5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _serve(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > end or not thread.is_alive():
            pytest.fail("a server did not start")
        time.sleep(0.05)
    return server, thread


def _user_uri(root_uri: str, user: str, password: str) -> str:
    netloc = urlsplit(root_uri).netloc.rsplit("@", 1)[-1]
    return f"mongodb://{user}:{password}@{netloc}/?authSource=admin"


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    container = MongoDbContainer(MONGO_IMAGE, username="root", password=secrets.token_hex(16))
    with container:
        uri = container.get_connection_url()
        client: RootClient = MongoClient(uri, serverSelectionTimeoutMS=5_000)
        try:
            _wait_for_ping(client)
        finally:
            client.close()
        yield uri


@pytest.fixture(scope="session")
def root_db(mongo_uri: str) -> Iterator[Any]:
    client: RootClient = MongoClient(mongo_uri)
    try:
        yield client[DATABASE]
    finally:
        client.close()


def _keyring(master: MasterKey, store: DataStore) -> Keyring:
    return Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))


def _notify(mongo_uri: str) -> tuple[Notify, NotifySink]:
    """The notify sink: a fresh master key and grant key, and a hire mobile in each org. Seeding runs on
    its own client (an async client stays on the event loop it first ran on; the server has its own)."""
    master = MasterKey(SecretKey.generate())
    store = connect(mongo_uri, DATABASE, app_name="mcp-notify")
    keyring = _keyring(master, store)
    signing = GrantSigningKey.generate()
    verifier = GrantVerifier([signing.public_key])
    policy = load_sink_policy(ROOT / "config" / "pii_sink_policy.yaml")
    hires = {"org_acme": "h_acme_001", "org_globex": "h_globex_001"}
    mobiles = {"org_acme": "9876501234", "org_globex": "9988776655"}

    async def seed() -> dict[str, str]:
        seeding = connect(mongo_uri, DATABASE, app_name="mcp-notify-seed")
        seed_keyring = _keyring(master, seeding)
        await ensure_grant_indexes(seeding)
        await ensure_outbox_indexes(seeding)
        await ensure_send_indexes(seeding)
        tokens = {}
        for org in ORGS:
            with tenant_scope(org):
                await seed_keyring.create(org)
            tokens[org] = await seed_vault_value(
                seed_keyring, seeding, org, subject_id=hires[org], field=VaultField.MOBILE, value=mobiles[org]
            )
        await seeding.close()
        return tokens

    def resolver(audit: GrantAudit) -> SinkResolver:
        return SinkResolver.create(keyring, store, verifier, policy, audit)

    events: list[str] = []
    sink = NotifySink(
        store,
        resolver=resolver,
        verifier=verifier,
        telegram=TelegramMockChannel(store, TelegramMockTransport()),
        events=DirectEvents(events),
        auditor=AuditLog(store),
    )
    return Notify(GrantIssuer(signing, policy), asyncio.run(seed()), hires, events), sink


def _register_agents(root_db: Any) -> None:
    """Registry records as `AgentRegistry.sync` writes them (sync itself needs a replica set)."""
    manifests = (*load_manifests(ROOT / "config" / "agents"), parse_manifest(XT_AGENT))
    now = datetime.now(UTC)
    root_db["orgs"].insert_many([{"_id": org, "name": org} for org in ORGS])
    root_db["agent_registry"].insert_many(
        [
            {
                "_id": m.name,
                "version": 1,
                "sha256": m.sha256(),
                "active": True,
                "manifest": m.document(),
                "updated_at": now,
            }
            for m in manifests
        ]
    )


@pytest.fixture(scope="session")
def stack(mongo_uri: str, root_db: Any) -> Iterator[Stack]:
    read_pw, write_pw = secrets.token_hex(16), secrets.token_hex(16)
    admin_client: RootClient = MongoClient(mongo_uri)
    try:
        provision(admin_client["admin"], DATABASE, read_password=read_pw, write_password=write_pw)
    finally:
        admin_client.close()
    _register_agents(root_db)
    token_key, context_key, mocks_key, notify_key = (secrets.token_urlsafe(40) for _ in range(4))
    mocks_store = InMemoryIdempotencyStore()
    notify, sink = _notify(mongo_uri)
    with captured_telemetry("mcp-hub-int", instrument=True) as telemetry:
        mongo_port, mocks_port, notify_port, hub_port = _free_port(), _free_port(), _free_port(), _free_port()
        mongo_server = build_server(
            connect(_user_uri(mongo_uri, "mcp_mongo_ro", read_pw), DATABASE, app_name="mcp-mongo"),
            context_key,
            write_store=connect(_user_uri(mongo_uri, "mcp_mongo_rw", write_pw), DATABASE, app_name="mcp-mongo"),
            port=mongo_port,
        )
        mocks_server = build_mocks_server(mocks_store, mocks_key, port=mocks_port)
        notify_server = build_notify_server(sink, notify_key, port=notify_port)
        hub_store = connect(mongo_uri, DATABASE, app_name="mcp-hub")
        audit = AuditLog(hub_store)
        gateway = Gateway(
            catalog=platform_catalog(),
            registry=AgentRegistry(MongoRegistryStore(hub_store), audit),
            auditor=audit,
            downstream=McpDownstream(
                {
                    "mongo": f"http://127.0.0.1:{mongo_port}/mcp",
                    MOCKS_SERVER: f"http://127.0.0.1:{mocks_port}/mcp",
                    NOTIFY_SERVER: f"http://127.0.0.1:{notify_port}/mcp",
                },
                context_key,
                keys={MOCKS_SERVER: mocks_key, NOTIFY_SERVER: notify_key},
            ),
            token_key=token_key,
            secrets=(context_key, mocks_key, notify_key, mongo_uri, read_pw, write_pw),
        )
        hub = build_hub(gateway, allowed_hosts=("127.0.0.1",))
        servers = [
            _serve(mongo_server.streamable_http_app(), mongo_port),
            _serve(mocks_server.streamable_http_app(), mocks_port),
            _serve(notify_server.streamable_http_app(), notify_port),
            _serve(hub.app, hub_port),
        ]
        try:
            yield Stack(
                f"http://127.0.0.1:{hub_port}/mcp",
                token_key,
                context_key,
                mongo_uri,
                telemetry,
                mocks_store,
                mocks_key,
                notify,
            )
        finally:
            for server, thread in reversed(servers):
                server.should_exit = True
                thread.join(timeout=10)
