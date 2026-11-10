"""Test support: an in-memory `UserStore` with the seed users of both orgs, an in-memory audit log,
and token helpers.

Test-only, like `onb_telemetry.testing`: production code never imports it. It lives in the package
so test suites can import it without making their `tests/` directories packages (two packages named
`tests` collide when several projects' tests run in one pytest call).

`InMemoryAuditLog` keeps `onb_data.AuditLog`'s contract for the API: records are per org (read in
the current tenant scope only), newest first, filtered exactly; it has no update or delete either.

The user store keeps its contract: the login directory is global, user records are read in the current
tenant scope only (a user of another org is "not found"), and an email is unique across orgs.
Synthetic people only (`evals/fixtures/seed_v1.json`); passwords are made up per test run.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from onb_contracts.auth import Role
from onb_data import Actor, AuditEntry, AuditOutcome, AuditPage, AuditQuery, current_tenant, tenant_scope

from onb_api.auth.passwords import hash_password
from onb_api.auth.settings import AuthSettings
from onb_api.auth.tokens import Principal, issue_token
from onb_api.auth.users import EmailTakenError, LoginEntry, NewUser, UserRecord, login_key

TEST_SETTINGS = AuthSettings(jwt_secret=secrets.token_urlsafe(48), bcrypt_rounds=4)
PASSWORD = secrets.token_urlsafe(12)

# (user_id, org_id, name, role) from seed_v1; emails are made up from the id.
SEED_USERS = [
    ("u_acme_hradmin", "org_acme", "Kavya Rao", Role.HR_ADMIN),
    ("u_acme_hr1", "org_acme", "Sameer Khan", Role.HR),
    ("u_acme_mgr_ravi", "org_acme", "Ravi Kumar", Role.MANAGER),
    ("u_globex_hr1", "org_globex", "Laura Chen", Role.HR),
    ("u_globex_mgr1", "org_globex", "Tom Becker", Role.MANAGER),
]


def email_of(user_id: str) -> str:
    return f"{user_id}@example.com"


class InMemoryUserStore:
    def __init__(self) -> None:
        self._logins: dict[str, LoginEntry] = {}
        self._users: dict[tuple[str, str], UserRecord] = {}
        self.login_lookups = 0
        self.indexes_ensured = 0

    async def ensure_indexes(self) -> None:
        self.indexes_ensured += 1

    async def find_login(self, email: str) -> LoginEntry | None:
        self.login_lookups += 1
        return self._logins.get(login_key(email))

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self._users.get((current_tenant().org_id, user_id))

    async def add_user(self, user: NewUser, *, user_id: str | None = None) -> UserRecord:
        org_id = current_tenant().org_id
        key = login_key(user.email)
        if key in self._logins:
            raise EmailTakenError("email already registered")
        record = UserRecord(
            user_id=user_id or f"u_{secrets.token_hex(8)}",
            org_id=org_id,
            email=user.email,
            name=user.name,
            role=user.role,
            password_hash=user.password_hash,
        )
        self._logins[key] = LoginEntry(org_id=org_id, user_id=record.user_id)
        self._users[(org_id, record.user_id)] = record
        return record

    def remove_user_record(self, org_id: str, user_id: str) -> None:
        """The record goes but the directory entry stays: a token or login for a deleted user."""
        self._users.pop((org_id, user_id))

    def set_role(self, org_id: str, user_id: str, role: Role) -> None:
        self._users[(org_id, user_id)] = replace(self._users[(org_id, user_id)], role=role)


async def seeded_store() -> InMemoryUserStore:
    store = InMemoryUserStore()
    password_hash = hash_password(PASSWORD, rounds=TEST_SETTINGS.bcrypt_rounds)
    for user_id, org_id, name, role in SEED_USERS:
        with tenant_scope(org_id):
            new = NewUser(email=email_of(user_id), name=name, role=role, password_hash=password_hash)
            await store.add_user(new, user_id=user_id)
    return store


def token_for(user_id: str, org_id: str, role: Role, *, now: datetime | None = None) -> str:
    principal = Principal(user_id=user_id, org_id=org_id, role=role)
    return issue_token(principal, TEST_SETTINGS, now=now or datetime.now(UTC)).access_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class InMemoryAuditLog:
    """`audit(...)` appends, `list(...)` reads the current org's records. `now`/`trace` stamp new records."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self.now: datetime | None = None
        self.trace: str | None = None
        self.indexes_ensured = 0
        self.list_calls = 0
        self.last_list: tuple[AuditQuery, int, int] | None = None

    async def ensure_indexes(self) -> None:
        self.indexes_ensured += 1

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str:
        entry = AuditEntry(
            id=uuid.uuid4().hex,
            org_id=current_tenant().org_id,
            actor=actor,
            action=action,
            target=target,
            outcome=outcome,
            reason=reason,
            trace_id=self.trace,
            at=self.now or datetime.now(UTC),
        )
        self._entries = [*self._entries, entry]
        return entry.id

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> AuditPage:
        self.list_calls += 1
        self.last_list = (query, page, limit)
        org_id = current_tenant().org_id
        matching = sorted(
            (e for e in self._entries if e.org_id == org_id and _matches(e, query)),
            key=lambda e: (e.at, e.id),
            reverse=True,
        )
        start = (page - 1) * limit
        return AuditPage(entries=tuple(matching[start : start + limit]), total=len(matching), page=page, limit=limit)


def _matches(entry: AuditEntry, query: AuditQuery) -> bool:
    exact = (
        (query.actor_type, entry.actor.type),
        (query.actor_id, entry.actor.id),
        (query.action, entry.action),
        (query.outcome, entry.outcome),
        (query.target, entry.target),
    )
    if any(wanted is not None and wanted != actual for wanted, actual in exact):
        return False
    if query.since is not None and entry.at < query.since:
        return False
    return query.until is None or entry.at <= query.until
