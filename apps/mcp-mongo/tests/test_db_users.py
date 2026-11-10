"""Least-privilege MongoDB users for mcp-mongo (PRD §7.5 defence in depth), without a database.

The roles are the outer bound on what any agent manifest can grant: a read-only and a read-write
user on named collections, and nothing at all on the vault, the keys, the PII access log or users.
"""

from __future__ import annotations

from typing import Any

import pytest
from onb_data import GLOBAL_COLLECTIONS
from onb_mcp_mongo import db_users
from onb_mcp_mongo.db_users import (
    DENIED_COLLECTIONS,
    READ_ONLY,
    READ_WRITE,
    PrivilegeError,
    check_privileges,
    provision,
    role_privileges,
)

DB = "onboarding"
VAULT_SET = {"pii_vault", "pii_vault_aadhaar", "org_keys", "pii_access_log", "users"}


def test_the_denied_collections_are_the_vault_keys_access_log_and_users() -> None:
    assert DENIED_COLLECTIONS == VAULT_SET


@pytest.mark.parametrize("spec", [READ_ONLY, READ_WRITE], ids=lambda s: s.user)
def test_no_role_names_a_denied_global_or_queue_collection(spec: db_users.AccessSpec) -> None:
    named = {p["resource"]["collection"] for p in role_privileges(spec, DB)}
    assert named == set(spec.collections)
    assert not named & DENIED_COLLECTIONS
    assert not named & GLOBAL_COLLECTIONS
    assert not named & {"audit_log", "jobs", "dead_letters", "events", "mock_outbox"}


def test_the_read_only_role_can_only_find() -> None:
    privileges = role_privileges(READ_ONLY, DB)
    assert privileges
    assert all(p["actions"] == ["find"] for p in privileges)
    assert all(p["resource"]["db"] == DB for p in privileges)


def test_the_read_write_role_can_find_insert_and_update_but_never_remove() -> None:
    privileges = role_privileges(READ_WRITE, DB)
    assert privileges
    assert all(p["actions"] == ["find", "insert", "update"] for p in privileges)
    assert set(READ_WRITE.collections) <= set(READ_ONLY.collections)


def test_the_two_users_and_roles_are_distinct() -> None:
    assert READ_ONLY.user != READ_WRITE.user
    assert READ_ONLY.role != READ_WRITE.role


def _privileges(spec: db_users.AccessSpec) -> list[dict[str, Any]]:
    return [dict(p) for p in role_privileges(spec, DB)]


def test_the_exact_role_privileges_pass_the_check() -> None:
    check_privileges(_privileges(READ_ONLY), READ_ONLY, DB)
    check_privileges(_privileges(READ_WRITE), READ_WRITE, DB)
    check_privileges(_privileges(READ_ONLY)[:1], READ_ONLY, DB)  # fewer is fine


@pytest.mark.parametrize(
    ("privilege", "why"),
    [
        ({"resource": {"db": DB, "collection": "new_hires"}, "actions": ["find", "insert"]}, "insert"),
        ({"resource": {"db": DB, "collection": "pii_vault"}, "actions": ["find"]}, "pii_vault"),
        ({"resource": {"db": DB, "collection": ""}, "actions": ["find"]}, "every collection"),
        ({"resource": {"db": "", "collection": "new_hires"}, "actions": ["find"]}, "every database"),
        ({"resource": {"db": "other", "collection": "new_hires"}, "actions": ["find"]}, "other"),
        ({"resource": {"cluster": True}, "actions": ["shutdown"]}, "cluster"),
        ({"resource": {"anyResource": True}, "actions": ["anyAction"]}, "any resource"),
    ],
)
def test_a_wider_read_only_privilege_is_refused(privilege: dict[str, Any], why: str) -> None:
    with pytest.raises(PrivilegeError, match=why):
        check_privileges([*_privileges(READ_ONLY), privilege], READ_ONLY, DB)


def test_the_read_write_user_may_not_remove() -> None:
    wider = {"resource": {"db": DB, "collection": "run_tasks"}, "actions": ["find", "remove"]}
    with pytest.raises(PrivilegeError, match="remove"):
        check_privileges([wider], READ_WRITE, DB)


class FakeAdmin:
    """Records the admin commands `provision` sends; knows which roles and users exist."""

    def __init__(self, *, roles: set[str] | None = None, users: set[str] | None = None) -> None:
        self.roles = roles or set()
        self.users = users or set()
        self.sent: list[dict[str, Any]] = []

    def command(self, name: str, value: Any = 1, /, **kwargs: Any) -> dict[str, Any]:
        self.sent.append({name: value, **kwargs})
        if name == "rolesInfo":
            return {"roles": [{"role": value}] if value in self.roles else []}
        if name == "usersInfo":
            return {"users": [{"user": value}] if value in self.users else []}
        return {"ok": 1}


PASSWORDS = {"read_password": "r" * 32, "write_password": "w" * 32}


def test_provision_creates_both_roles_and_users_on_an_empty_database() -> None:
    admin = FakeAdmin()
    provision(admin, DB, **PASSWORDS)
    created = [next(iter(cmd)) for cmd in admin.sent if next(iter(cmd)) in {"createRole", "createUser"}]
    assert created == ["createRole", "createRole", "createUser", "createUser"]
    users = {cmd["createUser"]: cmd for cmd in admin.sent if "createUser" in cmd}
    assert users[READ_ONLY.user]["roles"] == [{"role": READ_ONLY.role, "db": "admin"}]
    assert users[READ_WRITE.user]["roles"] == [{"role": READ_WRITE.role, "db": "admin"}]
    assert users[READ_ONLY.user]["pwd"] == PASSWORDS["read_password"]
    roles = {cmd["createRole"]: cmd for cmd in admin.sent if "createRole" in cmd}
    assert roles[READ_ONLY.role]["privileges"] == role_privileges(READ_ONLY, DB)
    assert roles[READ_ONLY.role]["roles"] == []


def test_provision_updates_existing_roles_and_users() -> None:
    admin = FakeAdmin(roles={READ_ONLY.role, READ_WRITE.role}, users={READ_ONLY.user, READ_WRITE.user})
    provision(admin, DB, **PASSWORDS)
    names = {next(iter(cmd)) for cmd in admin.sent}
    assert "createRole" not in names
    assert "createUser" not in names
    assert {"updateRole", "updateUser"} <= names


@pytest.mark.parametrize("bad", ["", "short"])
def test_provision_refuses_a_weak_password(bad: str) -> None:
    with pytest.raises(ValueError, match="at least") as caught:
        provision(FakeAdmin(), DB, read_password=bad, write_password="w" * 32)
    assert not bad or bad not in str(caught.value)


def test_provision_refuses_the_same_password_for_both_users() -> None:
    with pytest.raises(ValueError, match="differ"):
        provision(FakeAdmin(), DB, read_password="x" * 32, write_password="x" * 32)


class FakeClient:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.admin = FakeAdmin()
        self.closed = False
        CLIENTS.append(self)

    def __getitem__(self, name: str) -> FakeAdmin:
        assert name == "admin"
        return self.admin

    def close(self) -> None:
        self.closed = True


CLIENTS: list[FakeClient] = []
CLI_ENV = {
    "MCP_MONGO_ADMIN_URI": "mongodb://admin:apw@mongo:27017/?authSource=admin",
    "MCP_MONGO_READ_PASSWORD": "r" * 32,
    "MCP_MONGO_WRITE_PASSWORD": "w" * 32,
}


def test_the_provisioning_command_uses_the_admin_uri_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    CLIENTS.clear()
    monkeypatch.setattr(db_users, "MongoClient", FakeClient)
    db_users.main({**CLI_ENV, "MCP_MONGO_DATABASE": "onb_x"})
    (client,) = CLIENTS
    assert client.uri == CLI_ENV["MCP_MONGO_ADMIN_URI"]
    assert client.closed
    roles = [cmd for cmd in client.admin.sent if "createRole" in cmd]
    assert roles[0]["privileges"][0]["resource"]["db"] == "onb_x"
    out = capsys.readouterr().out
    assert "provisioned mcp_mongo_ro and mcp_mongo_rw" in out
    assert "r" * 32 not in out


@pytest.mark.parametrize("missing", sorted(CLI_ENV))
def test_the_provisioning_command_needs_each_setting(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setattr(db_users, "MongoClient", FakeClient)
    with pytest.raises(SystemExit, match=missing):
        db_users.main({k: v for k, v in CLI_ENV.items() if k != missing})
