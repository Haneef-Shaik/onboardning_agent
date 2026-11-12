"""Build and start the `make test-pii` platform over one scratch database (SEC-1). See `platform.py`.

Each service gets its own MongoDB client, made on the event loop it runs on (an async client stays on
the loop it first ran on): the API, the hub and the MCP servers on their uvicorn loops, the orchestrator
worker and the notify consumers on the `Background` loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.registry.mongo import ORGS, MongoRegistryStore
from onb_agents.runs import run_bus
from onb_agents.runtime import HubToolSource
from onb_api.app import create_app
from onb_api.approvals import ApprovalService
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.passwords import hash_password
from onb_api.auth.settings import AuthSettings
from onb_api.auth.users import NewUser
from onb_api.chat.setup import build_chat
from onb_api.documents.service import DocumentService
from onb_api.evals.store import EvalRunStore
from onb_api.hires.service import HireService
from onb_api.notify.service import BusDecisions, MockChannels
from onb_api.runs import RunService
from onb_api.templates import MongoTemplateStore, RegistryValidatorSource, TemplateLifecycle
from onb_contracts.notify import APPROVAL_NOTIFY_CONSUMER, IN_APP_CONSUMER
from onb_contracts.templates import Template
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    GrantIssuer,
    GrantVerifier,
    Keyring,
    RepositoryOrgKeyStore,
    load_sink_policy,
)
from onb_data import Actor, AuditLog, DataStore, connect, tenant_scope
from onb_llm import load_llm_config
from onb_mcp_hub.catalog import MOCKS_SERVER, NOTIFY_SERVER, platform_catalog
from onb_mcp_hub.downstream import McpDownstream
from onb_mcp_hub.gateway import Gateway
from onb_mcp_hub.server import build_hub
from onb_mcp_mocks.notify.approvals import approval_notify_handler
from onb_mcp_mocks.notify.in_app import in_app_handler
from onb_mcp_mocks.notify.main import NotifyService, build_service, ensure_indexes
from onb_mcp_mocks.notify.settings import NotifySettings
from onb_mcp_mocks.server import build_server as build_mocks_server
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mongo.server import build_server as build_mongo_server
from onb_orchestrator.agent_executor import AgentTaskExecutor
from onb_orchestrator.policy import RunPolicy
from onb_orchestrator.worker import prepare
from pymongo.database import Database

from .platform import (
    ASSISTANT_SCRIPT,
    ORG,
    ROOT,
    TEMPLATE_KEY,
    USERS,
    HireLookup,
    ScriptedModels,
    Secrets,
    llm_env,
    user_email,
)
from .servers import Background, serve_http

BCRYPT_ROUNDS = 4  # test speed; production keeps its default
POLICY_FILE = ROOT / "config" / "pii_sink_policy.yaml"


@dataclass(frozen=True)
class Platform:
    api_url: str
    hub_url: str
    database: str
    root_db: Database[dict[str, Any]]
    secrets: Secrets
    notify: NotifyService
    workers: Background


def _keyring(store: DataStore, keys: Secrets) -> Keyring:
    return Keyring(keys.master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))


async def seed(uri: str, database: str, keys: Secrets) -> None:
    """The org with its keys, three users, the agent registry and the published seed template."""
    store = connect(uri, database, app_name="pii-seed")
    try:
        await store.shared(ORGS).insert_one({"_id": ORG, "name": "PII scan org"})
        with tenant_scope(ORG):
            await _keyring(store, keys).create(ORG)
        users = MongoUserStore(store)
        await users.ensure_indexes()
        password_hash = hash_password(keys.password, rounds=BCRYPT_ROUNDS)
        for user_id, handle, name, role in USERS:
            with tenant_scope(ORG):
                user = NewUser(email=user_email(handle), name=name, role=role, password_hash=password_hash)
                await users.add_user(user, user_id=user_id)
        audit = AuditLog(store)
        manifests = load_manifests(ROOT / "config" / "agents")
        catalog = load_catalog(ROOT / "config" / "tool_catalog.yaml", manifests)
        registry = AgentRegistry(MongoRegistryStore(store), audit)
        await registry.sync(manifests, catalog, actor=Actor.user(ADMIN_ACTOR))
        lifecycle = TemplateLifecycle(
            MongoTemplateStore(store), RegistryValidatorSource.from_config(registry, ROOT / "config"), audit
        )
        await lifecycle.ensure_indexes()
        document = yaml.safe_load((ROOT / "templates" / f"{TEMPLATE_KEY}.yaml").read_text(encoding="utf-8"))
        with tenant_scope(ORG):
            draft = await lifecycle.create_draft(Template.model_validate(document), actor_id=ADMIN_ACTOR)
            await lifecycle.publish(draft.id, actor_id=ADMIN_ACTOR)
    finally:
        await store.close()


ADMIN_ACTOR = USERS[1][0]


def build_api(uri: str, database: str, keys: Secrets, hub_url: str, script_file: Path) -> FastAPI:
    """The app as `onb_api.main` wires it, with the production run assistant (CHAT-1) over the hub."""
    store = connect(uri, database, app_name="api")
    audit = AuditLog(store)
    users = MongoUserStore(store)
    registry = AgentRegistry(MongoRegistryStore(store), audit)
    keyring = _keyring(store, keys)
    hires = HireService.build(store, keyring, auditor=audit, users=users)
    templates = TemplateLifecycle(
        MongoTemplateStore(store), RegistryValidatorSource.from_config(registry, ROOT / "config"), audit
    )
    env = {**llm_env(script_file), "AGENT_TOKEN_KEY": keys.token_key, "MCP_HUB_URL": hub_url}
    llm_config = load_llm_config(ROOT / "config" / "llm.yaml", env)
    bus = run_bus(store)
    runs = RunService.build(store, bus, auditor=audit, hires=hires, templates=templates, bus_indexes=bus.ensure_indexes)
    chat = build_chat(env, store=store, runs=runs, registry=registry, audit_log=audit, llm_config=llm_config)
    assert chat is not None, "the run assistant must be wired (AGENT_TOKEN_KEY, MCP_HUB_URL)"
    return create_app(
        auth=AuthSettings(jwt_secret=keys.jwt, bcrypt_rounds=BCRYPT_ROUNDS),
        users=users,
        audit=audit,
        agents=registry,
        hires=hires,
        templates=templates,
        documents=DocumentService.build(store, keyring, auditor=audit),
        evals=EvalRunStore(store),
        llm=llm_config,
        notify=MockChannels(store, webhook_secret=keys.webhook, decisions=BusDecisions(store)),
        runs=runs,
        approvals=ApprovalService.build(store, bus, auditor=audit, bus_indexes=bus.ensure_indexes),
        chat=chat,
    )


def notify_settings(uri: str, database: str, keys: Secrets) -> NotifySettings:
    return NotifySettings(
        context_key=keys.notify_context,
        mongo_uri=uri,
        master_key=keys.master,
        verifier=GrantVerifier([keys.grant.public_key]),
        database=database,
        policy_file=POLICY_FILE,
        host="127.0.0.1",
        port=0,
    )


def hub_app(uri: str, database: str, keys: Secrets, downstream: Mapping[str, str]) -> Any:
    store = connect(uri, database, app_name="mcp-hub")
    audit = AuditLog(store)
    gateway = Gateway(
        catalog=platform_catalog(),
        registry=AgentRegistry(MongoRegistryStore(store), audit),
        auditor=audit,
        downstream=McpDownstream(
            dict(downstream),
            keys.mongo_context,
            keys={MOCKS_SERVER: keys.mocks_context, NOTIFY_SERVER: keys.notify_context},
        ),
        token_key=keys.token_key,
        secrets=(keys.mongo_context, keys.mocks_context, keys.notify_context, uri),
    )
    return build_hub(gateway, allowed_hosts=("127.0.0.1",)).app


def start_workers(uri: str, database: str, keys: Secrets, tmp: Path, hire: HireLookup, hub_url: str) -> Background:
    """The orchestrator worker (agent executor) and mcp-notify's consumers, on one background loop."""
    workers = Background()

    async def orchestrator() -> None:
        store = connect(uri, database, app_name="orchestrator")
        audit = AuditLog(store)
        executor = AgentTaskExecutor(
            store=store,
            agents=AgentRegistry(MongoRegistryStore(store), audit),
            models=ScriptedModels(tmp, hire),
            tools=HubToolSource(hub_url),
            auditor=audit,
            token_key=keys.token_key,
            grants=GrantIssuer(keys.grant, load_sink_policy(POLICY_FILE)),
            run_max_tokens=1_000_000,
        )
        policy = RunPolicy(max_attempts=1, lease_seconds=60)
        worker = await prepare(store, policy=policy, executor=executor, drain_seconds=5)
        try:
            await worker.run(workers.stop_event)
        finally:
            await store.close()

    async def notify_consumers() -> None:
        service = build_service(notify_settings(uri, database, keys))
        await ensure_indexes(service)
        handlers = {
            IN_APP_CONSUMER: in_app_handler(service.store),
            APPROVAL_NOTIFY_CONSUMER: approval_notify_handler(service.store, service.telegram),
        }
        try:
            await service.bus.worker(handlers).run(workers.stop_event)
        finally:
            await service.store.close()

    workers.start(orchestrator)
    workers.start(notify_consumers)
    return workers


@contextlib.contextmanager
def running_platform(
    uri: str, database: str, root_db: Database[dict[str, Any]], tmp: Path, hire: Callable[[], Mapping[str, Any]]
) -> Iterator[Platform]:
    keys = Secrets()
    asyncio.run(seed(uri, database, keys))
    script = tmp / "assistant-script.json"
    script.write_text(json.dumps(ASSISTANT_SCRIPT), encoding="utf-8")
    mongo = build_mongo_server(connect(uri, database, app_name="mcp-mongo"), keys.mongo_context, port=0)
    mocks = build_mocks_server(InMemoryIdempotencyStore(), keys.mocks_context, port=0)
    notify = build_service(notify_settings(uri, database, keys))
    with contextlib.ExitStack() as stack:
        mongo_url = stack.enter_context(serve_http(mongo.streamable_http_app(), "mcp-mongo"))
        mocks_url = stack.enter_context(serve_http(mocks.streamable_http_app(), "mcp-mocks"))
        notify_url = stack.enter_context(serve_http(notify.server.streamable_http_app(), "mcp-notify"))
        downstream = {"mongo": f"{mongo_url}/mcp", MOCKS_SERVER: f"{mocks_url}/mcp", NOTIFY_SERVER: f"{notify_url}/mcp"}
        hub_url = stack.enter_context(serve_http(hub_app(uri, database, keys, downstream), "mcp-hub")) + "/mcp"
        api_url = stack.enter_context(
            serve_http(build_api(uri, database, keys, hub_url, script), "api", access_log=True)
        )
        workers = start_workers(uri, database, keys, tmp, hire, hub_url)
        stack.callback(workers.stop)
        yield Platform(api_url, hub_url, database, root_db, keys, notify, workers)
