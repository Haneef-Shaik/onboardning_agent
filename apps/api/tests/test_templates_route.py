"""The template lifecycle over HTTP: draft → validate → publish → archive (TPL-2; PRD FR-T3, FR-T4, §10).

Box 1: HR publish → 403, HR_ADMIN publish → 200. Box 2: publishing an invalid draft → 422 with the
validator's errors. Box 3: a published version cannot be changed (through its draft, by publishing
over it, or by any write path to `template_versions`). Publish and archive are audited (AUD-1).
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_contracts.auth import Role
from onb_data import AuditQuery, tenant_scope

SEED_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"

ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
MANAGER = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def seed_template() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    return data


def _with_cycle(template: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(template)
    first = changed["tasks"][0]
    first["depends_on"] = [changed["tasks"][-1]["id"]]
    return changed


def _with_unknown_agent(template: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(template)
    changed["tasks"][2]["agent"] = "hr_bot"
    return changed


def _create(client: TestClient, template: dict[str, Any], who: tuple[str, str, Role] = HR) -> dict[str, Any]:
    response = client.post("/templates/drafts", json={"template": template}, headers=_auth(who))
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _publish(client: TestClient, draft_id: str, who: tuple[str, str, Role] = ADMIN) -> Any:
    return client.post(f"/templates/drafts/{draft_id}/publish", headers=_auth(who))


def _audit(log: InMemoryAuditLog, action: str) -> list[Any]:
    async def read() -> list[Any]:
        with tenant_scope("org_acme"):
            return list((await log.list(AuditQuery(action=action), page=1, limit=100)).entries)

    return asyncio.run(read())


# --- Box 1: only HR_ADMIN publishes ------------------------------------------------------------------


def test_hr_publish_is_403_and_hr_admin_publish_is_200(client: TestClient) -> None:
    draft = _create(client, seed_template())
    refused = _publish(client, draft["id"], HR)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "FORBIDDEN"
    assert _publish(client, draft["id"], MANAGER).status_code == 403
    # Nothing was published by the refused calls.
    assert client.get("/templates", headers=_auth(HR)).json()["data"] == []
    published = _publish(client, draft["id"], ADMIN)
    assert published.status_code == 200, published.text
    version = published.json()["data"]
    assert (version["key"], version["version"], version["status"]) == ("engineer-india", 1, "published")
    assert version["template"]["version"] == 1
    assert version["template"]["status"] == "published"
    assert version["published_by"] == "u_acme_hradmin"
    assert version["draft_id"] == draft["id"]


def test_publish_and_refusals_are_audited(client: TestClient, audit_log: InMemoryAuditLog) -> None:
    draft = _create(client, seed_template())
    assert _publish(client, draft["id"], HR).status_code == 403
    assert _publish(client, draft["id"], ADMIN).status_code == 200
    records = sorted(_audit(audit_log, "template.publish"), key=lambda e: e.at)
    assert [(e.actor.id, e.outcome.value, e.target) for e in records] == [
        ("u_acme_hr1", "denied", f"template_draft:{draft['id']}"),
        ("u_acme_hradmin", "success", "template:engineer-india/v1"),
    ]


def test_each_publish_appends_the_next_version(client: TestClient) -> None:
    first = _create(client, seed_template())
    assert _publish(client, first["id"]).json()["data"]["version"] == 1
    renamed = {**seed_template(), "name": "Engineers, India (2027)"}
    second = _create(client, renamed)
    assert _publish(client, second["id"]).json()["data"]["version"] == 2
    listing = client.get("/templates", headers=_auth(MANAGER)).json()["data"]
    assert [(t["key"], t["latest_version"], t["name"]) for t in listing] == [
        ("engineer-india", 2, "Engineers, India (2027)")
    ]
    assert [(v["version"], v["status"]) for v in listing[0]["versions"]] == [(1, "published"), (2, "published")]


def test_publishing_a_published_draft_again_returns_its_version(
    client: TestClient, audit_log: InMemoryAuditLog
) -> None:
    draft = _create(client, seed_template())
    first = _publish(client, draft["id"])
    again = _publish(client, draft["id"])
    assert again.status_code == 200
    assert again.json()["data"] == first.json()["data"]
    assert len(_audit(audit_log, "template.publish")) == 1  # the retry did nothing, so nothing new to audit


# --- Box 2: an invalid draft is not published ----------------------------------------------------------


def test_publishing_an_invalid_draft_returns_422_with_the_validators_errors(
    client: TestClient, audit_log: InMemoryAuditLog
) -> None:
    template = _with_unknown_agent(_with_cycle(seed_template()))
    draft = _create(client, template)
    assert {issue["rule"] for issue in draft["issues"]} == {"cycle", "unknown_agent"}
    response = _publish(client, draft["id"])
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    issues = {detail["issue"].split(":", 1)[0] for detail in error["details"]}
    assert issues == {"cycle", "unknown_agent"}
    assert "tasks.laptop" in {detail["field"] for detail in error["details"]}
    # Nothing was published, and the failure is audited.
    assert client.get("/templates", headers=_auth(ADMIN)).json()["data"] == []
    assert [(e.outcome.value, e.target) for e in _audit(audit_log, "template.publish")] == [
        ("failed", f"template_draft:{draft['id']}")
    ]


def test_publish_validates_against_the_registry_at_publish_time(client: TestClient, registry_retire: Any) -> None:
    draft = _create(client, seed_template())
    assert draft["issues"] == []
    registry_retire("provisioning_agent")
    response = _publish(client, draft["id"])
    assert response.status_code == 422
    assert {d["issue"].split(":", 1)[0] for d in response.json()["error"]["details"]} >= {"unknown_agent"}


def test_validate_reports_without_storing(client: TestClient) -> None:
    draft = _create(client, _with_cycle(seed_template()))
    report = client.post(f"/templates/drafts/{draft['id']}/validate", headers=_auth(HR))
    assert report.status_code == 200
    data = report.json()["data"]
    assert data["ok"] is False
    assert [issue["rule"] for issue in data["issues"]] == ["cycle"]
    assert data["order"] == []
    fixed = client.put(f"/templates/drafts/{draft['id']}", json={"template": seed_template()}, headers=_auth(HR))
    valid = client.post(f"/templates/drafts/{draft['id']}/validate", headers=_auth(HR)).json()["data"]
    assert fixed.json()["data"]["issues"] == []
    assert valid["ok"] is True
    assert valid["order"][0] == "collect_docs"


def test_a_draft_holding_a_sensitive_value_is_not_saved(client: TestClient) -> None:
    template = seed_template()
    template["tasks"][0]["inputs"] = {"note": "call 9876543210 first"}
    created = client.post("/templates/drafts", json={"template": template}, headers=_auth(HR))
    assert created.status_code == 422, created.text
    details = created.json()["error"]["details"]
    assert details
    assert all(d["issue"].startswith("sensitive_input") for d in details)
    assert "9876543210" not in created.text
    assert client.get("/templates/drafts", headers=_auth(HR)).json()["data"] == []


def test_a_draft_body_with_a_bad_shape_is_422(client: TestClient) -> None:
    response = client.post("/templates/drafts", json={"template": {"key": "x"}}, headers=_auth(HR))
    assert response.status_code == 422


# --- Box 3: a published version never changes ------------------------------------------------------------


def test_the_draft_of_a_published_version_cannot_be_changed(client: TestClient) -> None:
    draft = _create(client, seed_template())
    version = _publish(client, draft["id"]).json()["data"]
    changed = {**seed_template(), "name": "Changed after publishing"}
    response = client.put(f"/templates/drafts/{draft['id']}", json={"template": changed}, headers=_auth(ADMIN))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"
    stored = client.get("/templates/engineer-india/versions/1", headers=_auth(HR)).json()["data"]
    assert stored == version
    assert stored["template"]["name"] == "Engineers, India"


def test_publishing_again_never_overwrites_an_existing_version(client: TestClient) -> None:
    first = _publish(client, _create(client, seed_template())["id"]).json()["data"]
    _publish(client, _create(client, {**seed_template(), "name": "Second"})["id"])
    assert client.get("/templates/engineer-india/versions/1", headers=_auth(HR)).json()["data"] == first


def test_no_route_writes_to_a_version(client: TestClient) -> None:
    _publish(client, _create(client, seed_template())["id"])
    url = "/templates/engineer-india/versions/1"
    body = {"template": {**seed_template(), "name": "Edited"}}
    for method in ("PUT", "PATCH", "DELETE", "POST"):
        response = client.request(method, url, json=body, headers=_auth(ADMIN))
        assert response.status_code == 405, (method, response.text)
    assert client.get(url, headers=_auth(ADMIN)).json()["data"]["template"]["name"] == "Engineers, India"


def test_a_revision_mismatch_is_409(client: TestClient) -> None:
    draft = _create(client, seed_template())
    body = {"template": {**seed_template(), "name": "One"}, "revision": draft["revision"]}
    saved = client.put(f"/templates/drafts/{draft['id']}", json=body, headers=_auth(HR))
    assert saved.status_code == 200
    assert saved.json()["data"]["revision"] == draft["revision"] + 1
    stale = client.put(f"/templates/drafts/{draft['id']}", json=body, headers=_auth(HR))
    assert stale.status_code == 409


# --- archive ------------------------------------------------------------------------------------------------


def test_only_hr_admin_archives_and_archive_keeps_the_body(client: TestClient, audit_log: InMemoryAuditLog) -> None:
    version = _publish(client, _create(client, seed_template())["id"]).json()["data"]
    url = "/templates/engineer-india/versions/1/archive"
    assert client.post(url, headers=_auth(HR)).status_code == 403
    assert client.post(url, headers=_auth(MANAGER)).status_code == 403
    archived = client.post(url, headers=_auth(ADMIN))
    assert archived.status_code == 200, archived.text
    data = archived.json()["data"]
    assert data["status"] == "archived"
    assert data["archived_by"] == "u_acme_hradmin"
    assert data["template"] == {**version["template"], "status": "archived"}
    again = client.post(url, headers=_auth(ADMIN))
    assert again.json()["data"] == data
    records = sorted(_audit(audit_log, "template.archive"), key=lambda e: e.at)
    assert [(e.actor.id, e.outcome.value, e.target) for e in records] == [
        ("u_acme_hr1", "denied", "template:engineer-india/v1"),
        ("u_acme_mgr_ravi", "denied", "template:engineer-india/v1"),
        ("u_acme_hradmin", "success", "template:engineer-india/v1"),
    ]


def test_archiving_a_missing_version_is_404(client: TestClient) -> None:
    assert client.post("/templates/nothing-here/versions/1/archive", headers=_auth(ADMIN)).status_code == 404
    _publish(client, _create(client, seed_template())["id"])
    assert client.post("/templates/engineer-india/versions/2/archive", headers=_auth(ADMIN)).status_code == 404


# --- drafts and roles -------------------------------------------------------------------------------------


def test_managers_cannot_touch_drafts(client: TestClient) -> None:
    draft = _create(client, seed_template())
    for method, url in [
        ("POST", "/templates/drafts"),
        ("GET", "/templates/drafts"),
        ("GET", f"/templates/drafts/{draft['id']}"),
        ("PUT", f"/templates/drafts/{draft['id']}"),
        ("POST", f"/templates/drafts/{draft['id']}/validate"),
    ]:
        response = client.request(method, url, json={"template": seed_template()}, headers=_auth(MANAGER))
        assert response.status_code == 403, (method, url)


def test_a_draft_is_read_back_and_listed(client: TestClient) -> None:
    draft = _create(client, seed_template())
    assert draft["status"] == "draft"
    assert draft["template"]["status"] == "draft"
    assert draft["template"]["version"] is None
    assert (draft["created_by"], draft["revision"]) == ("u_acme_hr1", 1)
    assert client.get(f"/templates/drafts/{draft['id']}", headers=_auth(ADMIN)).json()["data"] == draft
    assert [d["id"] for d in client.get("/templates/drafts", headers=_auth(ADMIN)).json()["data"]] == [draft["id"]]


def test_lifecycle_fields_in_a_draft_body_are_ignored(client: TestClient) -> None:
    draft = _create(client, {**seed_template(), "version": 7, "status": "published"})
    assert (draft["template"]["version"], draft["template"]["status"]) == (None, "draft")
    assert _publish(client, draft["id"]).json()["data"]["version"] == 1


@pytest.mark.parametrize("draft_id", ["tdr_" + "0" * 32, "not-a-draft-id"])
def test_an_unknown_or_malformed_draft_id(client: TestClient, draft_id: str) -> None:
    response = client.get(f"/templates/drafts/{draft_id}", headers=_auth(HR))
    assert response.status_code in {404, 422}
    assert response.status_code == (404 if draft_id.startswith("tdr_") else 422)


def test_another_orgs_draft_is_not_found(client: TestClient) -> None:
    draft = _create(client, seed_template())
    globex_hr = ("u_globex_hr1", "org_globex", Role.HR)
    globex_admin = ("u_globex_xt_admin", "org_globex", Role.HR_ADMIN)
    assert client.get(f"/templates/drafts/{draft['id']}", headers=_auth(globex_hr)).status_code == 404
    assert _publish(client, draft["id"], globex_admin).status_code == 404
    assert client.get("/templates/drafts", headers=_auth(globex_hr)).json()["data"] == []


def test_a_version_that_already_exists_is_409_not_500(
    client: TestClient, template_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out of sync head and versions (never in normal flow): the insert-only store refuses, the caller
    gets a conflict."""
    from onb_api.templates.store import VersionExistsError

    async def refuse(_: Any) -> None:
        raise VersionExistsError("engineer-india v1 is already published")

    monkeypatch.setattr(template_store, "insert_version", refuse)
    response = _publish(client, _create(client, seed_template())["id"])
    assert response.status_code == 409, response.text
    assert client.get("/templates", headers=_auth(ADMIN)).json()["data"] == []


def test_a_denied_archive_of_an_out_of_range_version_is_audited_as_invalid(
    client: TestClient, audit_log: InMemoryAuditLog
) -> None:
    assert client.post("/templates/engineer-india/versions/5000000/archive", headers=_auth(HR)).status_code == 403
    assert [e.target for e in _audit(audit_log, "template.archive")] == ["template:invalid"]
