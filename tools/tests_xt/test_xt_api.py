"""Every API route: an org-A actor cannot read, list, update or infer org-B data (PRD §7.9 Testing).

`ROUTE_CASES` has one row per (method, path) that `create_app()` serves; `test_xt_coverage.py`
fails the run when a route has no row. Each row gives the ordinary request an org-A actor makes
(`request`) and the route's own cross-tenant checks (`check`). Every row also gets the generic checks
below, for each org-A role:

* the response carries none of org B's seeded strings (`assert_no_leak`);
* naming org B in a query parameter or header changes nothing, and in a body is ignored or refused.

A route that takes an id must also show that org B's id answers exactly like an id that does not
exist (`assert_indistinguishable`: 404 either way, never 403); a route that creates something unique
must show that a value taken in org B does not collide (no unique-index inference).
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient
from onb_api.auth.tokens import read_token
from onb_api.documents.bucket import DocumentNotFoundError
from onb_api.documents.meta import META_COLLECTION
from onb_api.documents.service import DocumentService
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, bearer, token_for
from onb_contracts.auth import Role
from onb_data import tenant_scope

from .conftest import Unscoped, World, xt_keyring
from .world import (
    A_DRAFT,
    B_DRAFT,
    B_ENGINEER_VERSIONS,
    B_ONLY_KEY,
    B_SALES_DRAFT,
    ORG_A,
    ORG_B,
    SHARED_KEY,
    XT_VAULT_HIRES,
    Seen,
    assert_indistinguishable,
    assert_no_leak,
    audit_rows,
    base_template,
    email_of,
    template_drafts,
    tenant_documents,
    without_volatile,
)

Request = dict[str, Any]  # keyword arguments for `TestClient.request`

# One org-A actor per role (seed_v1 ids).
ACTORS: Mapping[Role, str] = {Role.HR_ADMIN: "u_acme_hradmin", Role.HR: "u_acme_hr1", Role.MANAGER: "u_acme_mgr_ravi"}
# An org-B HR_ADMIN: seed_v1 has none, and /audit does not need a stored user.
B_ADMIN = ("u_globex_xt_admin", ORG_B, Role.HR_ADMIN)

# Ways a caller might try to name another org. None of them may change what the route does.
ORG_PARAMS = ("org_id", "orgId", "org", "tenant", "tenant_id")
ORG_HEADERS = ("X-Org-Id", "X-Tenant-Id", "X-Org")


class Response(Protocol):
    """What the suite reads of a test-client response."""

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...


def _token(role: Role) -> str:
    return token_for(ACTORS[role], ORG_A, role)


def seen(response: Response, *, volatile: frozenset[str] = frozenset()) -> Seen:
    body = response.json()
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = {**body, "data": {k: v for k, v in body["data"].items() if k not in volatile}}
    return Seen(response.status_code, without_volatile(body))


@dataclass(frozen=True)
class RouteCase:
    request: Callable[[World, Role], Request]
    """The ordinary request an org-A actor with this role makes."""
    check: Callable[[TestClient, World, Unscoped], None]
    """The route's own cross-tenant assertions (`unscoped` is for root snapshots of org B)."""
    expected: Mapping[Role, int]
    """The status each org-A role gets for `request`: the generic checks hold it to that, so a check
    that only ever compared two refusals cannot pass for a role that should succeed."""
    volatile: frozenset[str] = field(default=frozenset())
    """Fields of `data` that differ on every call by design (a fresh token)."""


# --- GET /health -----------------------------------------------------------------------------------


def _health_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/health", "headers": bearer(_token(role))}


def _health_check(client: TestClient, _: World) -> None:
    """Nothing about any org: the same answer to org A, org B and no token at all."""
    as_a = seen(client.get("/health", headers=bearer(_token(Role.HR_ADMIN))))
    as_b = seen(client.get("/health", headers=bearer(token_for(*B_ADMIN))))
    anonymous = seen(client.get("/health"))
    assert as_a == as_b == anonymous


# --- POST /auth/login --------------------------------------------------------------------------------


def _login_request(xt: World, role: Role) -> Request:
    return {"method": "POST", "url": "/auth/login", "json": {"email": email_of(ACTORS[role]), "password": xt.password}}


def _login_check(client: TestClient, xt: World) -> None:
    # Logging in as an org-A user yields an org-A token, whatever org the body names.
    plain = client.post("/auth/login", json=_login_request(xt, Role.HR)["json"])
    assert plain.status_code == 200, plain.text
    assert read_token(plain.json()["data"]["access_token"], TEST_SETTINGS).org_id == ORG_A
    steered = client.post("/auth/login", json={**_login_request(xt, Role.HR)["json"], "org_id": ORG_B})
    if steered.status_code == 200:
        assert read_token(steered.json()["data"]["access_token"], TEST_SETTINGS).org_id == ORG_A
    else:
        assert steered.status_code == 422, steered.text
    # An org-B email with a wrong password answers exactly like an email nobody has.
    foreign = client.post("/auth/login", json={"email": email_of("u_globex_hr1"), "password": "not-the-password"})
    missing = client.post("/auth/login", json={"email": email_of("u_nobody_xt"), "password": "not-the-password"})
    assert_indistinguishable(seen(foreign), seen(missing), "POST /auth/login with an org-B email")


# --- GET /me -------------------------------------------------------------------------------------------


def _me_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/me", "headers": bearer(_token(role))}


def _me_check(client: TestClient, _: World) -> None:
    for role, user_id in ACTORS.items():
        data = client.get("/me", headers=bearer(_token(role))).json()["data"]
        assert (data["user_id"], data["org_id"]) == (user_id, ORG_A)
    # A valid org-A token naming an org-B user finds nobody, exactly like a user that does not exist.
    foreign = client.get("/me", headers=bearer(token_for("u_globex_hr1", ORG_A, Role.HR)))
    missing = client.get("/me", headers=bearer(token_for("u_nobody_xt", ORG_A, Role.HR)))
    assert foreign.status_code == 401
    assert_indistinguishable(seen(foreign), seen(missing), "GET /me for an org-B user id")


# --- GET /audit ----------------------------------------------------------------------------------------


def _audit_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/audit", "headers": bearer(_token(role)), "params": {"limit": 100}}


def _audit(client: TestClient, params: Mapping[str, Any], token: str | None = None) -> Response:
    return client.get("/audit", params=params, headers=bearer(token or _token(Role.HR_ADMIN)))


def _audit_check(client: TestClient, _: World) -> None:
    body = _audit(client, {"limit": 100}).json()
    assert {r["org_id"] for r in body["data"]} == {ORG_A}
    assert len(body["data"]) == len(audit_rows(ORG_A))
    # Filtering on org B's actors, actions or targets finds exactly what a value nobody has finds.
    for key, foreign_value, missing_value in [
        ("actor_id", "u_globex_hr1", "u_nobody_xt"),
        ("actor_id", "globex_doc_agent", "nobody_xt_agent"),
        ("action", "xt.globex_only", "xt.nobody_has_this"),
        ("target", "hire:h_globex_001/pan", "hire:h_nobody_xt/pan"),
    ]:
        foreign, missing = _audit(client, {key: foreign_value}), _audit(client, {key: missing_value})
        assert foreign.json()["data"] == []
        assert_indistinguishable(seen(foreign), seen(missing), f"GET /audit?{key}=<org B's>")
    # Paging past org A's records finds nothing, not org B's.
    assert _audit(client, {"page": 2, "limit": len(audit_rows(ORG_A))}).json()["data"] == []
    # And org B's admin, symmetrically, sees only org B.
    b_body = _audit(client, {"limit": 100}, token_for(*B_ADMIN)).json()
    assert {r["org_id"] for r in b_body["data"]} == {ORG_B}


# --- GET /agents, GET /agents/{name} (REG-1) ---------------------------------------------------------
# The registry is global: what an admin sees may not depend on the admin's org, and no record may
# carry an org. A name that exists only in org B's data answers like a name nobody has: 404, not 403.

KNOWN_AGENT = "db_agent"


def _agents_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/agents", "headers": bearer(_token(role))}


def _agent_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": f"/agents/{KNOWN_AGENT}", "headers": bearer(_token(role))}


def _same_for_both_orgs(client: TestClient, url: str) -> dict[str, Any]:
    as_a = client.get(url, headers=bearer(_token(Role.HR_ADMIN)))
    as_b = client.get(url, headers=bearer(token_for(*B_ADMIN)))
    assert as_a.status_code == 200, as_a.text
    assert seen(as_a) == seen(as_b), f"GET {url} depends on the caller's org"
    assert "org_id" not in as_a.text, f"GET {url}: a global record names an org"
    body: dict[str, Any] = as_a.json()
    return body


def _agents_check(client: TestClient, _: World) -> None:
    body = _same_for_both_orgs(client, "/agents")
    assert KNOWN_AGENT in {agent["name"] for agent in body["data"]}


def _agent_check(client: TestClient, _: World) -> None:
    assert _same_for_both_orgs(client, f"/agents/{KNOWN_AGENT}")["data"]["name"] == KNOWN_AGENT
    admin = bearer(_token(Role.HR_ADMIN))
    foreign = client.get("/agents/globex_doc_agent", headers=admin)  # an org-B actor name (AUDIT_ROWS)
    missing = client.get("/agents/nobody_xt_agent", headers=admin)
    assert foreign.status_code == 404, foreign.json()
    assert_indistinguishable(seen(foreign), seen(missing), "GET /agents/{name} for an org-B name")


# --- HIRE-1: hires, PII update and reveal, the PII access log -------------------------------------------
# Each org has one vault-backed hire (`xt.vault_hires()[org]`, values in `world.XT_VAULT_HIRES`); org A's is
# Ravi's report. Org B's hire, its values, and its access-log entry must never reach an org-A actor;
# its id answers exactly like an id nobody has (404 for every role that may ask).

MISSING_HIRE = "h_nobody_xt"
_new_emails = itertools.count(1)


def _new_hire_body() -> dict[str, Any]:
    # A fresh email per call (the route refuses a second hire with one email); the mask stays the same.
    return {
        "name": "Ishaan Rao",
        "role": "analyst",
        "location": "Kochi",
        "start_date": "2026-12-21",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": f"xnew{next(_new_emails):05d}@example.com", "mobile": "+91 90000 00064"},
    }


def _create_hire_request(xt: World, role: Role) -> Request:
    xt.vault_hires()  # org B's vault hire exists, so a leak of it would show
    return {"method": "POST", "url": "/hires", "headers": bearer(_token(role)), "json": _new_hire_body()}


def _create_hire_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    hr = bearer(_token(Role.HR))
    # An email org B already uses is free in org A: no unique-index inference across orgs.
    taken_in_b = {**_new_hire_body(), "pii": {"email": XT_VAULT_HIRES[ORG_B]["pii"]["email"]}}
    created = client.post("/hires", json=taken_in_b, headers=hr)
    assert created.status_code == 201, created.text
    new_id = created.json()["data"]["id"]
    # Org B's manager answers exactly like a manager nobody has.
    foreign = client.post("/hires", json={**_new_hire_body(), "manager_id": "u_globex_mgr1"}, headers=hr)
    missing = client.post("/hires", json={**_new_hire_body(), "manager_id": "u_nobody_xt"}, headers=hr)
    assert foreign.status_code == 422
    assert_indistinguishable(seen(foreign), seen(missing), "POST /hires with org B's manager")
    # The new hire is org A's: org B's admin cannot see it.
    assert client.get(f"/hires/{new_id}", headers=bearer(token_for(*B_ADMIN))).status_code == 404
    b_ids = {h["id"] for h in client.get("/hires", headers=bearer(token_for(*B_ADMIN))).json()["data"]}
    assert new_id not in b_ids
    assert xt.vault_hires()[ORG_B] in b_ids


def _list_hires_request(xt: World, role: Role) -> Request:
    xt.vault_hires()
    return {"method": "GET", "url": "/hires", "headers": bearer(_token(role)), "params": {"limit": 200}}


def _list_hires_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    a_ids = {
        h["id"] for h in client.get("/hires", headers=bearer(_token(Role.HR)), params={"limit": 200}).json()["data"]
    }
    seeded_a = {doc["_id"] for doc in tenant_documents(ORG_A)["new_hires"]}
    assert a_ids == seeded_a | {xt.vault_hires()[ORG_A]}
    mine = client.get("/hires", headers=bearer(_token(Role.MANAGER))).json()["data"]
    assert {h["manager_id"] for h in mine} == {ACTORS[Role.MANAGER]}
    b_ids = {h["id"] for h in client.get("/hires", headers=bearer(token_for(*B_ADMIN))).json()["data"]}
    assert b_ids == {doc["_id"] for doc in tenant_documents(ORG_B)["new_hires"]} | {xt.vault_hires()[ORG_B]}


def _get_hire_request(xt: World, role: Role) -> Request:
    return {"method": "GET", "url": f"/hires/{xt.vault_hires()[ORG_A]}", "headers": bearer(_token(role))}


def _get_hire_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    for role in ACTORS:
        headers = bearer(_token(role))
        missing = client.get(f"/hires/{MISSING_HIRE}", headers=headers)
        for foreign_id in (xt.vault_hires()[ORG_B], "h_globex_001"):
            foreign = client.get(f"/hires/{foreign_id}", headers=headers)
            assert foreign.status_code == 404, foreign.json()
            assert_indistinguishable(seen(foreign), seen(missing), f"GET /hires/{{id}} of org B as {role.value}")


def _update_pii_request(xt: World, role: Role) -> Request:
    return {
        "method": "PUT",
        "url": f"/hires/{xt.vault_hires()[ORG_A]}/pii/mobile",
        "headers": bearer(_token(role)),
        "json": {"value": "+91 90000 00063"},
    }


def _update_pii_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    for role in (Role.HR_ADMIN, Role.HR):
        headers = bearer(_token(role))
        missing = client.put(f"/hires/{MISSING_HIRE}/pii/mobile", json={"value": "+91 90000 00063"}, headers=headers)
        foreign = client.put(
            f"/hires/{xt.vault_hires()[ORG_B]}/pii/mobile", json={"value": "+91 90000 00063"}, headers=headers
        )
        assert foreign.status_code == 404, foreign.json()
        assert_indistinguishable(seen(foreign), seen(missing), f"PUT /hires/{{id}}/pii of org B as {role.value}")
    # Org B's value is untouched: its own admin still reveals the original.
    b_value = _reveal(client, xt.vault_hires()[ORG_B], "mobile", bearer(token_for(*B_ADMIN))).json()["data"]["value"]
    assert b_value == XT_VAULT_HIRES[ORG_B]["pii"]["mobile"]


def _reveal(client: TestClient, hire_id: str, field: str, headers: dict[str, str]) -> Response:
    return client.post(f"/hires/{hire_id}/pii/{field}/reveal", json={"reason": "xt check"}, headers=headers)


def _reveal_request(xt: World, role: Role) -> Request:
    return {
        "method": "POST",
        "url": f"/hires/{xt.vault_hires()[ORG_A]}/pii/email/reveal",
        "headers": bearer(_token(role)),
        "json": {"reason": "xt check"},
    }


def _reveal_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    for role in (Role.HR_ADMIN, Role.HR):
        headers = bearer(_token(role))
        own = _reveal(client, xt.vault_hires()[ORG_A], "email", headers)
        assert own.json()["data"]["value"] == XT_VAULT_HIRES[ORG_A]["pii"]["email"]
        missing = _reveal(client, MISSING_HIRE, "email", headers)
        for foreign_id in (xt.vault_hires()[ORG_B], "h_globex_001"):
            foreign = _reveal(client, foreign_id, "email", headers)
            assert foreign.status_code == 404, foreign.json()
            assert_indistinguishable(seen(foreign), seen(missing), f"reveal of org B's hire as {role.value}")


def _access_log_request(xt: World, role: Role) -> Request:
    xt.vault_hires()  # org B's access log holds its seeded reveal
    return {"method": "GET", "url": "/pii-access-log", "headers": bearer(_token(role)), "params": {"limit": 200}}


def _access_log_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    admin = bearer(_token(Role.HR_ADMIN))
    _reveal(client, xt.vault_hires()[ORG_A], "pan", admin)
    entries = client.get("/pii-access-log", headers=admin).json()["data"]
    assert [(e["hire_id"], e["field"]) for e in entries] == [(xt.vault_hires()[ORG_A], "pan")]
    # Filtering on org B's hire finds exactly what a hire nobody has finds.
    foreign = client.get("/pii-access-log", params={"hire_id": xt.vault_hires()[ORG_B]}, headers=admin)
    missing = client.get("/pii-access-log", params={"hire_id": MISSING_HIRE}, headers=admin)
    assert foreign.json()["data"] == []
    assert_indistinguishable(seen(foreign), seen(missing), "GET /pii-access-log?hire_id=<org B's>")
    # And org B's admin, symmetrically, sees only org B's seeded reveal.
    b_entries = client.get("/pii-access-log", headers=bearer(token_for(*B_ADMIN))).json()["data"]
    assert [(e["hire_id"], e["field"], e["reason"]) for e in b_entries] == [
        (xt.vault_hires()[ORG_B], "email", "xt seed")
    ]


# --- /templates (TPL-2) ---------------------------------------------------------------------------------
# Org A has `engineer-india` v1 and an open draft; org B has `engineer-india` v1-v3, `globex-xt-sales`
# v1 and an open draft (`world.template_drafts`). Org B's drafts and versions answer org A exactly like
# ones that do not exist (404), org A's version numbers never count org B's, and no call by org A
# changes any org-B document.

MISSING_DRAFT = "tdr_" + "0" * 32
DRAFT_VOLATILE = frozenset({"id", "revision", "created_at", "updated_at"})


def _admin() -> dict[str, str]:
    return bearer(_token(Role.HR_ADMIN))


def _a_draft_ids() -> set[str]:
    return {draft_id for draft_id, _, _ in template_drafts(ORG_A)}


def _unchanged_for_b(xt: World, unscoped: Unscoped, call: Callable[[], None]) -> None:
    """Run `call` (org A's requests) and show that no org-B document changed."""
    before = xt.org_b_snapshot(unscoped)
    call()
    assert xt.org_b_snapshot(unscoped) == before, "an org-A call changed org B's documents"


def _foreign_like_missing(client: TestClient, method: str, foreign: str, missing: str, **kw: Any) -> None:
    as_foreign = client.request(method, foreign, headers=_admin(), **kw)
    as_missing = client.request(method, missing, headers=_admin(), **kw)
    assert as_foreign.status_code == 404, as_foreign.text
    assert_indistinguishable(seen(as_foreign), seen(as_missing), f"{method} {foreign}")


def _create_draft_request(_: World, role: Role) -> Request:
    return {
        "method": "POST",
        "url": "/templates/drafts",
        "headers": bearer(_token(role)),
        "json": {"template": base_template()},
    }


def _create_draft_check(client: TestClient, xt: World) -> None:
    # A key org B uses is free in org A: no unique-index inference across orgs.
    b_key = {**base_template(), "key": B_ONLY_KEY}
    created = client.post("/templates/drafts", json={"template": b_key}, headers=_admin())
    assert created.status_code == 201, created.text
    listed = client.get("/templates/drafts", headers=_admin()).json()["data"]
    assert {draft["id"] for draft in listed} == _a_draft_ids() | {created.json()["data"]["id"]}


def _list_drafts_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/templates/drafts", "headers": bearer(_token(role))}


def _list_drafts_check(client: TestClient, _: World) -> None:
    listed = client.get("/templates/drafts", headers=_admin()).json()["data"]
    assert {draft["id"] for draft in listed} == _a_draft_ids()
    b_admin = client.get("/templates/drafts", headers=bearer(token_for(*B_ADMIN))).json()["data"]
    assert {draft["id"] for draft in b_admin} == {draft_id for draft_id, _, _ in template_drafts(ORG_B)}


def _get_draft_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": f"/templates/drafts/{A_DRAFT}", "headers": bearer(_token(role))}


def _get_draft_check(client: TestClient, _: World) -> None:
    for draft_id in (B_DRAFT, B_SALES_DRAFT):
        _foreign_like_missing(client, "GET", f"/templates/drafts/{draft_id}", f"/templates/drafts/{MISSING_DRAFT}")


def _put_draft_request(_: World, role: Role) -> Request:
    return {
        "method": "PUT",
        "url": f"/templates/drafts/{A_DRAFT}",
        "headers": bearer(_token(role)),
        "json": {"template": base_template()},
    }


def _put_draft_check(client: TestClient, xt: World, unscoped: Unscoped) -> None:
    body = {"template": {**base_template(), "name": "Overwritten by org A"}}

    def call() -> None:
        for draft_id in (B_DRAFT, B_SALES_DRAFT):
            foreign, missing = f"/templates/drafts/{draft_id}", f"/templates/drafts/{MISSING_DRAFT}"
            _foreign_like_missing(client, "PUT", foreign, missing, json=body)

    _unchanged_for_b(xt, unscoped, call)


def _validate_request(_: World, role: Role) -> Request:
    return {"method": "POST", "url": f"/templates/drafts/{A_DRAFT}/validate", "headers": bearer(_token(role))}


def _validate_check(client: TestClient, _: World) -> None:
    foreign, missing = f"/templates/drafts/{B_DRAFT}/validate", f"/templates/drafts/{MISSING_DRAFT}/validate"
    _foreign_like_missing(client, "POST", foreign, missing)


def _publish_request(_: World, role: Role) -> Request:
    return {"method": "POST", "url": f"/templates/drafts/{A_DRAFT}/publish", "headers": bearer(_token(role))}


def _publish_check(client: TestClient, xt: World, unscoped: Unscoped) -> None:
    def call() -> None:
        foreign, missing = f"/templates/drafts/{B_DRAFT}/publish", f"/templates/drafts/{MISSING_DRAFT}/publish"
        _foreign_like_missing(client, "POST", foreign, missing)
        # Org B holds engineer-india v1-v3; org A's next version is its own v2.
        published = client.post(f"/templates/drafts/{A_DRAFT}/publish", headers=_admin())
        assert published.status_code == 200, published.text
        assert (published.json()["data"]["key"], published.json()["data"]["version"]) == (SHARED_KEY, 2)

    _unchanged_for_b(xt, unscoped, call)


def _list_templates_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": "/templates", "headers": bearer(_token(role))}


def _list_templates_check(client: TestClient, _: World) -> None:
    listed = client.get("/templates", headers=_admin()).json()["data"]
    assert [(t["key"], t["latest_version"], len(t["versions"])) for t in listed] == [(SHARED_KEY, 1, 1)]
    b_listed = client.get("/templates", headers=bearer(token_for(*B_ADMIN))).json()["data"]
    assert [(t["key"], t["latest_version"]) for t in b_listed] == [(SHARED_KEY, B_ENGINEER_VERSIONS), (B_ONLY_KEY, 1)]


def _version_request(_: World, role: Role) -> Request:
    return {"method": "GET", "url": f"/templates/{SHARED_KEY}/versions/1", "headers": bearer(_token(role))}


def _version_check(client: TestClient, _: World) -> None:
    mine = client.get(f"/templates/{SHARED_KEY}/versions/1", headers=_admin()).json()["data"]
    assert mine["draft_id"] in _a_draft_ids()
    # A key only org B has, and a version number only org B has reached, look like nothing at all.
    _foreign_like_missing(client, "GET", f"/templates/{B_ONLY_KEY}/versions/1", "/templates/nobody-xt/versions/1")
    _foreign_like_missing(
        client, "GET", f"/templates/{SHARED_KEY}/versions/{B_ENGINEER_VERSIONS}", f"/templates/{SHARED_KEY}/versions/99"
    )


def _archive_request(_: World, role: Role) -> Request:
    return {"method": "POST", "url": f"/templates/{SHARED_KEY}/versions/1/archive", "headers": bearer(_token(role))}


def _archive_check(client: TestClient, xt: World, unscoped: Unscoped) -> None:
    def call() -> None:
        foreign_only = f"/templates/{B_ONLY_KEY}/versions/1/archive"
        _foreign_like_missing(client, "POST", foreign_only, "/templates/nobody-xt/versions/1/archive")
        foreign_number = f"/templates/{SHARED_KEY}/versions/{B_ENGINEER_VERSIONS}/archive"
        _foreign_like_missing(client, "POST", foreign_number, f"/templates/{SHARED_KEY}/versions/99/archive")
        archived = client.post(f"/templates/{SHARED_KEY}/versions/1/archive", headers=_admin())
        assert archived.json()["data"]["status"] == "archived", archived.text

    _unchanged_for_b(xt, unscoped, call)
    # Org B's v1 of the shared key is still published.
    b_version = client.get(f"/templates/{SHARED_KEY}/versions/1", headers=bearer(token_for(*B_ADMIN)))
    assert b_version.json()["data"]["status"] == "published"


def _reads_only(check: Callable[[TestClient, World], None]) -> Callable[[TestClient, World, Unscoped], None]:
    """A check that takes no root snapshot."""

    def run(client: TestClient, xt: World, _: Unscoped) -> None:
        check(client, xt)

    return run


# --- HIRE-2: document scans ---------------------------------------------------------------------------------
# A scan goes to a hire of the caller's org only; org B's hire answers like a hire nobody has, and org B's
# scan neither opens for org A nor shows in org A's `documents_meta` (read through the scoped repository).

XT_SCAN = b"\x89PNG\r\n\x1a\n" + b"xt synthetic scan " * 64


def _upload(client: TestClient, hire_id: str, headers: dict[str, str]) -> Response:
    return client.post(
        f"/hires/{hire_id}/documents",
        params={"type": "pan"},
        content=XT_SCAN,
        headers={**headers, "Content-Type": "image/png"},
    )


def _upload_request(xt: World, role: Role) -> Request:
    return {
        "method": "POST",
        "url": f"/hires/{xt.vault_hires()[ORG_A]}/documents",
        "params": {"type": "pan"},
        "content": XT_SCAN,
        "headers": {**bearer(_token(role)), "Content-Type": "image/png"},
    }


def _upload_check(client: TestClient, xt: World) -> None:
    xt.vault_hires()  # seed both orgs' vault hires before anything is read
    a_hire, b_hire = xt.vault_hires()[ORG_A], xt.vault_hires()[ORG_B]
    b_upload = _upload(client, b_hire, bearer(token_for(*B_ADMIN)))
    assert b_upload.status_code == 201, b_upload.json()
    b_doc = b_upload.json()["data"]["id"]
    for role in (Role.HR_ADMIN, Role.HR):
        headers = bearer(_token(role))
        missing = _upload(client, MISSING_HIRE, headers)
        for foreign_id in (b_hire, "h_globex_001"):
            foreign = _upload(client, foreign_id, headers)
            assert foreign.status_code == 404, foreign.json()
            assert_indistinguishable(seen(foreign), seen(missing), f"upload to org B's hire as {role.value}")
    a_doc = _upload(client, a_hire, bearer(_token(Role.HR))).json()["data"]["id"]
    assert _documents_meta(xt, ORG_A) == {(a_doc, a_hire)}
    assert _documents_meta(xt, ORG_B) == {(b_doc, b_hire)}
    # Org B's scan does not open in org A, even named by id and hire; it still opens in org B.
    assert _read_scan(xt, ORG_A, b_hire, b_doc) is None
    assert _read_scan(xt, ORG_B, b_hire, b_doc) == XT_SCAN


def _documents_meta(xt: World, org_id: str) -> set[tuple[str, str]]:
    async def main() -> set[tuple[str, str]]:
        store = xt.store()
        try:
            with tenant_scope(org_id):
                return {(d["_id"], d["hire_id"]) for d in await store.tenant(META_COLLECTION).find({})}
        finally:
            await store.close()

    return asyncio.run(main())


def _read_scan(xt: World, org_id: str, hire_id: str, document_id: str) -> bytes | None:
    async def main() -> bytes | None:
        store = xt.store()
        try:
            documents = DocumentService.build(store, xt_keyring(store), auditor=InMemoryAuditLog())
            with tenant_scope(org_id):
                return await documents.read(org_id, hire_id=hire_id, document_id=document_id)
        except DocumentNotFoundError:
            return None
        finally:
            await store.close()

    return asyncio.run(main())


ALL_200: Mapping[Role, int] = dict.fromkeys(ACTORS, 200)
ADMIN_ONLY: Mapping[Role, int] = {Role.HR_ADMIN: 200, Role.HR: 403, Role.MANAGER: 403}
HR_ONLY: Mapping[Role, int] = {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403}
AUTHORS: Mapping[Role, int] = {Role.HR_ADMIN: 200, Role.HR: 200, Role.MANAGER: 403}
AUTHORS_CREATE: Mapping[Role, int] = {Role.HR_ADMIN: 201, Role.HR: 201, Role.MANAGER: 403}

ROUTE_CASES: Mapping[tuple[str, str], RouteCase] = {
    ("GET", "/health"): RouteCase(_health_request, _reads_only(_health_check), ALL_200),
    ("POST", "/auth/login"): RouteCase(
        _login_request, _reads_only(_login_check), ALL_200, volatile=frozenset({"access_token", "expires_at"})
    ),
    ("GET", "/me"): RouteCase(_me_request, _reads_only(_me_check), ALL_200),
    ("GET", "/audit"): RouteCase(_audit_request, _reads_only(_audit_check), ADMIN_ONLY),
    ("GET", "/agents"): RouteCase(_agents_request, _reads_only(_agents_check), ADMIN_ONLY),
    ("GET", "/agents/{name}"): RouteCase(_agent_request, _reads_only(_agent_check), ADMIN_ONLY),
    ("POST", "/hires"): RouteCase(
        _create_hire_request,
        _reads_only(_create_hire_check),
        {Role.HR_ADMIN: 201, Role.HR: 201, Role.MANAGER: 403},
        volatile=frozenset({"id", "created_at"}),
    ),
    ("GET", "/hires"): RouteCase(_list_hires_request, _reads_only(_list_hires_check), ALL_200),
    ("GET", "/hires/{hire_id}"): RouteCase(_get_hire_request, _reads_only(_get_hire_check), ALL_200),
    ("PUT", "/hires/{hire_id}/pii/{field}"): RouteCase(_update_pii_request, _reads_only(_update_pii_check), HR_ONLY),
    ("POST", "/hires/{hire_id}/pii/{field}/reveal"): RouteCase(
        _reveal_request, _reads_only(_reveal_check), HR_ONLY, volatile=frozenset({"access_id"})
    ),
    ("GET", "/pii-access-log"): RouteCase(_access_log_request, _reads_only(_access_log_check), ADMIN_ONLY),
    ("POST", "/templates/drafts"): RouteCase(
        _create_draft_request, _reads_only(_create_draft_check), AUTHORS_CREATE, volatile=DRAFT_VOLATILE
    ),
    ("GET", "/templates/drafts"): RouteCase(_list_drafts_request, _reads_only(_list_drafts_check), AUTHORS),
    ("GET", "/templates/drafts/{draft_id}"): RouteCase(_get_draft_request, _reads_only(_get_draft_check), AUTHORS),
    ("PUT", "/templates/drafts/{draft_id}"): RouteCase(
        _put_draft_request, _put_draft_check, AUTHORS, volatile=DRAFT_VOLATILE
    ),
    ("POST", "/templates/drafts/{draft_id}/validate"): RouteCase(
        _validate_request, _reads_only(_validate_check), AUTHORS
    ),
    ("POST", "/templates/drafts/{draft_id}/publish"): RouteCase(_publish_request, _publish_check, ADMIN_ONLY),
    ("GET", "/templates"): RouteCase(_list_templates_request, _reads_only(_list_templates_check), ALL_200),
    ("GET", "/templates/{key}/versions/{version}"): RouteCase(_version_request, _reads_only(_version_check), ALL_200),
    ("POST", "/templates/{key}/versions/{version}/archive"): RouteCase(_archive_request, _archive_check, ADMIN_ONLY),
    ("POST", "/hires/{hire_id}/documents"): RouteCase(
        _upload_request,
        _reads_only(_upload_check),
        {Role.HR_ADMIN: 201, Role.HR: 201, Role.MANAGER: 403},
        volatile=frozenset({"id", "uploaded_at"}),
    ),
}

_ROUTE_IDS = [f"{method} {path}" for method, path in ROUTE_CASES]


@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_the_routes_own_cross_tenant_checks(
    api: TestClient, xt_world: World, unscoped: Unscoped, route: tuple[str, str]
) -> None:
    ROUTE_CASES[route].check(api, xt_world, unscoped)


@pytest.mark.parametrize("role", list(ACTORS), ids=lambda r: r.value)
@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_no_org_b_data_reaches_an_org_a_actor(
    api: TestClient, xt_world: World, route: tuple[str, str], role: Role
) -> None:
    response = api.request(**ROUTE_CASES[route].request(xt_world, role))
    assert response.status_code == ROUTE_CASES[route].expected[role], response.text
    assert_no_leak(response.text, f"{route} as {role.value}")
    assert_no_leak(repr(sorted(response.headers.items())), f"{route} headers as {role.value}")


@pytest.mark.parametrize("role", list(ACTORS), ids=lambda r: r.value)
@pytest.mark.parametrize("route", list(ROUTE_CASES), ids=_ROUTE_IDS)
def test_naming_org_b_in_the_request_changes_nothing(
    api: TestClient, xt_world: World, route: tuple[str, str], role: Role
) -> None:
    case = ROUTE_CASES[route]
    # A fresh request each time: a creating route's request differs per call (a new unique email),
    # every other route's request is the same each time.
    request = case.request(xt_world, role)
    baseline = seen(api.request(**request), volatile=case.volatile)
    assert baseline.status == case.expected[role], baseline
    request = case.request(xt_world, role)
    params = {**request.get("params", {}), **dict.fromkeys(ORG_PARAMS, ORG_B)}
    headers = {**request.get("headers", {}), **dict.fromkeys(ORG_HEADERS, ORG_B)}
    steered = seen(api.request(**{**request, "params": params, "headers": headers}), volatile=case.volatile)
    assert steered == baseline, f"{route}: an org named in the query or headers changed the answer"
    request = case.request(xt_world, role)
    if "json" in request:
        body = {**request["json"], **dict.fromkeys(ORG_PARAMS, ORG_B)}
        in_body = api.request(**{**request, "json": body})
        assert in_body.status_code == 422 or seen(in_body, volatile=case.volatile) == baseline, in_body.text
        assert_no_leak(in_body.text, f"{route} with org B named in the body")
