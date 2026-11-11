"""The agent registry against a real MongoDB (REG-1 boxes 2 and 4; PRD FR-A1, U8).

Each test uses a scratch database on the compose Mongo, dropped afterwards. The shipped YAML is synced
through `MongoRegistryStore` and `AuditLog`, a manifest is edited and synced again, and the result is
read back with the root client (so the test does not trust the registry to report on itself) and
through `GET /agents`. A failed audit write mid-sync must leave neither registry changes nor audit
records behind: the transaction rolls both back.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry, SyncReport, load_catalog, load_manifests
from onb_agents.registry.__main__ import main as sync_command
from onb_agents.registry.mongo import MongoRegistryStore
from onb_api import create_app
from onb_api.testing import TEST_SETTINGS, InMemoryUserStore, bearer, token_for
from onb_contracts.auth import Role
from onb_data import Actor, AuditLog, AuditOutcome, connect
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root read-back across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]
ROOT = Path(__file__).resolve().parents[3]
ADMIN = Actor.user("u_acme_hradmin")
ORGS = ["org_acme", "org_globex"]


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_reg1_{uuid.uuid4().hex[:12]}"
    root_client[name]["orgs"].insert_many([{"_id": org} for org in ORGS])
    yield _uri(root_client), name
    root_client.drop_database(name)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "config" / "agents", tmp_path / "agents")
    shutil.copy(ROOT / "config" / "tool_catalog.yaml", tmp_path / "tool_catalog.yaml")
    return tmp_path


class FailingAuditLog(AuditLog):
    """The real log, failing on the n-th write (a database error mid-sync)."""

    fail_on = 0
    calls = 0

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        type(self).calls += 1
        if type(self).calls == self.fail_on:
            raise ConnectionError("audit write failed")
        return await super().audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)


def _sync(scratch: tuple[str, str], config: Path, *, log_class: type[AuditLog] = AuditLog) -> SyncReport:
    async def run() -> SyncReport:
        store = connect(*scratch)
        try:
            registry = AgentRegistry(MongoRegistryStore(store), log_class(store))
            manifests = load_manifests(config / "agents")
            return await registry.sync(manifests, load_catalog(config / "tool_catalog.yaml", manifests), actor=ADMIN)
        finally:
            await store.close()

    return asyncio.run(run())


def test_changing_a_yaml_and_resyncing_bumps_the_version_and_audits_it_in_mongo(
    scratch: tuple[str, str], config: Path, root_client: Client, unscoped: Unscoped
) -> None:
    db = root_client[scratch[1]]
    _sync(scratch, config)
    assert db["agent_registry"].find_one({"_id": "db_agent"}, {"version": 1}) == {"_id": "db_agent", "version": 1}
    assert db["agent_registry"].count_documents({}) == 6
    assert db["tool_catalog"].find_one({"_id": "tool_catalog"}, {"_id": 0, "version": 1}) == {"version": 1}

    path = config / "agents" / "db_agent.yaml"
    path.write_text(path.read_text().replace("max_steps: 8", "max_steps: 6"))
    report = _sync(scratch, config)

    stored = db["agent_registry"].find_one({"_id": "db_agent"})
    assert stored is not None
    assert (stored["version"], stored["manifest"]["limits"]["max_steps"]) == (2, 6)
    assert [(c.kind, c.target) for c in report.changes] == [("update", "agent:db_agent/v2")]
    with unscoped("root read-back of both orgs' audit records proves the change was audited in every org"):
        updates = list(db["audit_log"].find({"action": "registry.agent_update"}, {"_id": 0, "org_id": 1, "target": 1}))
    assert sorted(u["org_id"] for u in updates) == ORGS
    assert {u["target"] for u in updates} == {"agent:db_agent/v2"}
    record = db["audit_log"].find_one({"action": "registry.agent_update", "org_id": "org_acme"})
    assert record is not None
    assert record["actor"] == {"type": "user", "id": "u_acme_hradmin"}
    assert record["outcome"] == "success"
    assert record["reason"] == f"sha256:{stored['sha256'][:16]}"
    assert db["agent_registry"].count_documents({"version": 1}) == 5


def test_a_failed_audit_write_rolls_back_the_registry_and_the_audit_records(
    scratch: tuple[str, str], config: Path, root_client: Client, unscoped: Unscoped
) -> None:
    FailingAuditLog.fail_on, FailingAuditLog.calls = 4, 0
    with pytest.raises(ConnectionError, match="audit write failed"):
        _sync(scratch, config, log_class=FailingAuditLog)
    db = root_client[scratch[1]]
    assert db["agent_registry"].count_documents({}) == 0
    assert db["tool_catalog"].count_documents({}) == 0
    with unscoped("root count across every org proves the rollback left no audit record in any org"):
        assert db["audit_log"].count_documents({}) == 0


def test_the_sync_command_writes_the_registry(scratch: tuple[str, str], root_client: Client) -> None:
    env = {"MONGO_URI": scratch[0], "MONGO_DATABASE": scratch[1]}
    assert sync_command(["sync", "--actor", "u_acme_hradmin", "--config", str(ROOT / "config")], env=env) == 0
    assert sync_command(["sync", "--actor", "u_acme_hradmin", "--config", str(ROOT / "config")], env=env) == 0
    db = root_client[scratch[1]]
    assert db["agent_registry"].count_documents({"version": 1}) == 6
    assert db["audit_log"].count_documents({"org_id": "org_acme"}) == 7


@pytest.fixture
def api(scratch: tuple[str, str], config: Path) -> Iterator[TestClient]:
    _sync(scratch, config)
    store = connect(*scratch)
    log = AuditLog(store)
    registry = AgentRegistry(MongoRegistryStore(store), log)
    app = create_app(auth=TEST_SETTINGS, users=InMemoryUserStore(), audit=log, agents=registry)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(("role", "status"), [(Role.HR_ADMIN, 200), (Role.HR, 403), (Role.MANAGER, 403)])
def test_get_agents_is_hr_admin_only_against_the_real_registry(api: TestClient, role: Role, status: int) -> None:
    for path in ("/agents", "/agents/db_agent"):
        response = api.get(path, headers=bearer(token_for("u_acme_x", "org_acme", role)))
        assert response.status_code == status, response.text


def test_hr_admin_sees_every_agent_from_mongo(api: TestClient) -> None:
    body = api.get("/agents", headers=bearer(token_for("u_globex_admin", "org_globex", Role.HR_ADMIN))).json()
    assert len(body["data"]) == 6
    assert {a["version"] for a in body["data"]} == {1}
