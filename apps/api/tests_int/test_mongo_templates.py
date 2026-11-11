"""The template lifecycle against a real MongoDB (TPL-2; PRD FR-T4, §9).

Each test uses a scratch database on the compose replica set, dropped afterwards, with the registry
synced from `config/` (REG-1) and the API built as `onb_api.main` builds it. Results are read back
with the root client inside `unscoped(...)`, so the test does not trust the store to report on itself.

- A published version's stored document never changes: not through its draft, not by archiving, not
  by publishing again, and the unique `(org_id, key, version)` index refuses a second write.
- A publish whose audit write fails leaves no head, no version and an unlocked draft (one transaction).
- Version numbers are per org: two orgs publishing the same key each get v1.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.registry.mongo import MongoRegistryStore
from onb_api import create_app
from onb_api.templates import MongoTemplateStore, RegistryValidatorSource, TemplateLifecycle
from onb_api.templates.store import VersionExistsError
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, InMemoryUserStore, bearer, token_for
from onb_contracts.auth import Role
from onb_data import Actor, AuditLog, AuditOutcome, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]
ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config"
SEED: dict[str, Any] = yaml.safe_load((ROOT / "templates" / "engineer-india.yaml").read_text())
ADMIN_A = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR_A = ("u_acme_hr1", "org_acme", Role.HR)
ADMIN_B = ("u_globex_it_admin", "org_globex", Role.HR_ADMIN)


class FailingAuditLog(AuditLog):
    """The real audit log, except that writes for `failing` actions raise."""

    failing: frozenset[str] = frozenset({"template.publish"})

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        if action in self.failing and outcome is AuditOutcome.SUCCESS:
            raise RuntimeError("audit store unavailable")
        return await super().audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[str]:
    name = f"it_tpl2_{uuid.uuid4().hex[:12]}"
    root_client[name]["orgs"].insert_many([{"_id": "org_acme"}, {"_id": "org_globex"}])
    yield name
    root_client.drop_database(name)


async def _sync_registry(uri: str, database: str) -> None:
    """The registry synced from config/, through a store of its own (an async client keeps to one loop)."""
    store = connect(uri, database, app_name="it-tpl2-sync")
    try:
        registry = AgentRegistry(MongoRegistryStore(store), InMemoryAuditLog())
        manifests = load_manifests(CONFIG / "agents")
        catalog = load_catalog(CONFIG / "tool_catalog.yaml", manifests)
        await registry.sync(manifests, catalog, actor=Actor.user("u_acme_hradmin"))
    finally:
        await store.close()


def _api(root_client: Client, database: str, audit_type: type[AuditLog] = AuditLog) -> TestClient:
    asyncio.run(_sync_registry(_uri(root_client), database))
    store = connect(_uri(root_client), database, app_name="it-tpl2")
    registry = AgentRegistry(MongoRegistryStore(store), InMemoryAuditLog())
    audit = audit_type(store)
    lifecycle = TemplateLifecycle(
        MongoTemplateStore(store), RegistryValidatorSource.from_config(registry, CONFIG), audit
    )
    app = create_app(auth=TEST_SETTINGS, users=InMemoryUserStore(), audit=audit, agents=registry, templates=lifecycle)
    return TestClient(app)


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def _draft(client: TestClient, template: dict[str, Any], who: tuple[str, str, Role] = HR_A) -> str:
    response = client.post("/templates/drafts", json={"template": template}, headers=_auth(who))
    assert response.status_code == 201, response.text
    draft_id: str = response.json()["data"]["id"]
    return draft_id


def _versions(root_client: Client, database: str, unscoped: Unscoped) -> list[dict[str, Any]]:
    with unscoped("root read-back of the stored versions, to check the store did not change them"):
        return sorted(root_client[database]["template_versions"].find({}), key=lambda d: (d["org_id"], d["version"]))


def test_a_published_version_document_never_changes(root_client: Client, scratch: str, unscoped: Unscoped) -> None:
    with _api(root_client, scratch) as client:
        draft_id = _draft(client, SEED)
        published = client.post(f"/templates/drafts/{draft_id}/publish", headers=_auth(ADMIN_A))
        assert published.status_code == 200, published.text
        stored = _versions(root_client, scratch, unscoped)
        assert [(d["org_id"], d["key"], d["version"]) for d in stored] == [("org_acme", "engineer-india", 1)]

        changed = {**SEED, "name": "Changed after publishing"}
        edit = client.put(f"/templates/drafts/{draft_id}", json={"template": changed}, headers=_auth(ADMIN_A))
        assert edit.status_code == 409, edit.text
        again = client.post(f"/templates/drafts/{draft_id}/publish", headers=_auth(ADMIN_A))
        assert again.json()["data"] == published.json()["data"]
        second = client.post(f"/templates/drafts/{_draft(client, changed)}/publish", headers=_auth(ADMIN_A))
        assert second.json()["data"]["version"] == 2
        archived = client.post("/templates/engineer-india/versions/1/archive", headers=_auth(ADMIN_A))
        assert archived.json()["data"]["status"] == "archived"

    after = _versions(root_client, scratch, unscoped)
    assert after[0] == stored[0]  # byte for byte: archiving and later publishes left v1 alone
    assert after[0]["template"]["name"] == "Engineers, India"
    assert [d["version"] for d in after] == [1, 2]


def test_the_unique_index_refuses_a_second_write_of_a_version(
    root_client: Client, scratch: str, unscoped: Unscoped
) -> None:
    with _api(root_client, scratch) as client:
        client.post(f"/templates/drafts/{_draft(client, SEED)}/publish", headers=_auth(ADMIN_A))
    before = _versions(root_client, scratch, unscoped)

    async def rewrite() -> None:
        store = connect(_uri(root_client), scratch, app_name="it-tpl2-rewrite")
        templates = MongoTemplateStore(store)
        try:
            with tenant_scope("org_acme"):
                stored = await templates.get_version("engineer-india", 1)
                assert stored is not None
                rewritten = replace(stored, id=uuid.uuid4().hex, template={**stored.template, "name": "Rewritten"})
                with pytest.raises(VersionExistsError):
                    await templates.insert_version(rewritten)
        finally:
            await store.close()

    asyncio.run(rewrite())
    assert _versions(root_client, scratch, unscoped) == before


def test_a_publish_whose_audit_write_fails_leaves_nothing(
    root_client: Client, scratch: str, unscoped: Unscoped
) -> None:
    with _api(root_client, scratch, FailingAuditLog) as client:
        draft_id = _draft(client, SEED)
        response = client.post(f"/templates/drafts/{draft_id}/publish", headers=_auth(ADMIN_A))
        assert response.status_code == 500
        assert client.get(f"/templates/drafts/{draft_id}", headers=_auth(HR_A)).json()["data"]["status"] == "draft"
    with unscoped("root read-back: the rolled-back publish wrote no head, version or audit record"):
        database = root_client[scratch]
        assert database["templates"].count_documents({}) == 0
        assert database["template_versions"].count_documents({}) == 0
        assert database["audit_log"].count_documents({"action": "template.publish"}) == 0


def test_each_org_numbers_its_own_versions(root_client: Client, scratch: str, unscoped: Unscoped) -> None:
    with _api(root_client, scratch) as client:
        for who, author in ((ADMIN_A, HR_A), (ADMIN_B, ADMIN_B), (ADMIN_A, HR_A)):
            client.post(f"/templates/drafts/{_draft(client, SEED, author)}/publish", headers=_auth(who))
        listing_b = client.get("/templates", headers=_auth(ADMIN_B)).json()["data"]
    assert [(t["key"], t["latest_version"]) for t in listing_b] == [("engineer-india", 1)]
    stored = _versions(root_client, scratch, unscoped)
    assert [(d["org_id"], d["version"]) for d in stored] == [("org_acme", 1), ("org_acme", 2), ("org_globex", 1)]


def test_an_invalid_publish_is_422_and_audited_as_failed(root_client: Client, scratch: str, unscoped: Unscoped) -> None:
    broken = {**SEED, "tasks": [{**SEED["tasks"][0], "agent": "hr_bot"}, *SEED["tasks"][1:]]}
    with _api(root_client, scratch) as client:
        draft_id = _draft(client, broken)
        response = client.post(f"/templates/drafts/{draft_id}/publish", headers=_auth(ADMIN_A))
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["issue"].startswith("unknown_agent")
    with unscoped("root read-back of the audit record and the absence of a version"):
        database = root_client[scratch]
        assert database["template_versions"].count_documents({}) == 0
        records = list(database["audit_log"].find({"action": "template.publish"}))
    assert [(r["org_id"], r["outcome"], r["target"]) for r in records] == [
        ("org_acme", "failed", f"template_draft:{draft_id}")
    ]


class FailingArchiveAuditLog(FailingAuditLog):
    failing = frozenset({"template.archive"})


def test_an_archive_whose_audit_write_fails_leaves_the_version_published(
    root_client: Client, scratch: str, unscoped: Unscoped
) -> None:
    with _api(root_client, scratch, FailingArchiveAuditLog) as client:
        client.post(f"/templates/drafts/{_draft(client, SEED)}/publish", headers=_auth(ADMIN_A))
        response = client.post("/templates/engineer-india/versions/1/archive", headers=_auth(ADMIN_A))
        assert response.status_code == 500
        assert (
            client.get("/templates/engineer-india/versions/1", headers=_auth(HR_A)).json()["data"]["status"]
            == "published"
        )
    with unscoped("root read-back: the rolled-back archive left the head and the audit log untouched"):
        database = root_client[scratch]
        head = database["templates"].find_one({"key": "engineer-india"})
        assert head is not None
        assert [entry["archived_at"] for entry in head["versions"]] == [None]
        assert database["audit_log"].count_documents({"action": "template.archive"}) == 0
