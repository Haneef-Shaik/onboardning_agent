"""Least-privilege MongoDB users for mcp-mongo (PRD §7.5 defence in depth).

The policy layer decides what one agent may do; these roles bound what the server itself can do,
so a policy bug can't exceed them. There are two users:

- `mcp_mongo_ro` with role `mcp_mongo_read`: `find` on the named tenant collections agents may read.
- `mcp_mongo_rw` with role `mcp_mongo_write`: `find`, `insert` and `update` on the named collections
  agents may write. No `remove`, no index, no admin action, on anything.

Neither role names `pii_vault`, `pii_vault_aadhaar`, `org_keys`, `pii_access_log` or `users`, so
MongoDB refuses any MCP access to them. The roles and users live in `admin`; the privileges name
the application database. An agent manifest can only narrow these sets (HUB-1, REG-1).

`provision()` creates or updates the roles and users with an admin connection (run it with
`python -m onb_mcp_mongo.db_users`). `assert_least_privilege()` runs at server start and refuses
a connection whose user holds anything beyond its role, such as a root URI by mistake.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pymongo import MongoClient

from .settings import DEFAULT_DATABASE

__all__ = [
    "DENIED_COLLECTIONS",
    "READ_ONLY",
    "READ_WRITE",
    "AccessSpec",
    "PrivilegeError",
    "assert_least_privilege",
    "check_auth_info",
    "check_privileges",
    "provision",
    "role_privileges",
]

ROLE_DATABASE = "admin"
MIN_PASSWORD_LENGTH = 24

# Never reachable by any MCP user, whatever a manifest says (PRD §7.4, §7.10).
DENIED_COLLECTIONS = frozenset({"pii_vault", "pii_vault_aadhaar", "org_keys", "pii_access_log", "users"})

# Tenant collections an agent manifest may list (PRD §7.3, §9). Everything not listed is refused by
# MongoDB: the denied set, the global collections, audit_log (the hub appends it), the queues,
# events and the mock outbox.
_READABLE = (
    "new_hires",
    "onboarding_runs",
    "run_tasks",
    "approvals",
    "documents_meta",
    "templates",
    "template_versions",
    "knowledge_docs",
    "conversations",
    "messages",
)
_WRITABLE = ("new_hires", "run_tasks", "documents_meta")


@dataclass(frozen=True)
class AccessSpec:
    """One MCP database user, its role and what the role allows."""

    user: str
    role: str
    collections: tuple[str, ...]
    actions: tuple[str, ...]


READ_ONLY = AccessSpec(user="mcp_mongo_ro", role="mcp_mongo_read", collections=_READABLE, actions=("find",))
READ_WRITE = AccessSpec(
    user="mcp_mongo_rw", role="mcp_mongo_write", collections=_WRITABLE, actions=("find", "insert", "update")
)


class PrivilegeError(RuntimeError):
    """A connection holds more than its role allows. The message names the privilege, never a secret."""


class AdminDatabase(Protocol):
    def command(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]: ...


def role_privileges(spec: AccessSpec, database: str) -> list[dict[str, Any]]:
    """The role's privileges: the spec's actions on each named collection of `database`."""
    return [
        {"resource": {"db": database, "collection": name}, "actions": list(spec.actions)} for name in spec.collections
    ]


def _resource_label(resource: Mapping[str, Any]) -> str:
    if resource.get("cluster"):
        return "the cluster"
    if resource.get("anyResource"):
        return "any resource"
    db, coll = resource.get("db"), resource.get("collection")
    if not db:
        return "every database"
    if not coll:
        return f"every collection of {db}"
    return f"{db}.{coll}"


def check_privileges(privileges: Iterable[Mapping[str, Any]], spec: AccessSpec, database: str) -> None:
    """Raise `PrivilegeError` unless every privilege is within `spec` on `database`."""
    allowed_actions = set(spec.actions)
    allowed_collections = set(spec.collections) - DENIED_COLLECTIONS
    for privilege in privileges:
        resource = privilege.get("resource", {})
        label = _resource_label(resource)
        if resource.get("db") != database or resource.get("collection") not in allowed_collections:
            raise PrivilegeError(f"{spec.user} holds a privilege on {label}, outside its role")
        extra = sorted(set(privilege.get("actions", [])) - allowed_actions)
        if extra:
            raise PrivilegeError(f"{spec.user} may {', '.join(extra)} on {label}, outside its role")


def _exists(admin: AdminDatabase, kind: str, name: str) -> bool:
    key = "roles" if kind == "rolesInfo" else "users"
    return bool(admin.command(kind, name).get(key))


def _check_passwords(read_password: str, write_password: str) -> None:
    for which, password in (("read", read_password), ("write", write_password)):
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"the {which} user's password must be at least {MIN_PASSWORD_LENGTH} characters")
    if read_password == write_password:
        raise ValueError("the read and write users' passwords must differ")


def provision(admin: AdminDatabase, database: str, *, read_password: str, write_password: str) -> None:
    """Create or update both roles and both users. `admin` is the `admin` database of a client
    that may manage roles and users. Safe to run again; it resets the roles to exactly the spec."""
    _check_passwords(read_password, write_password)
    for spec in (READ_ONLY, READ_WRITE):
        verb = "updateRole" if _exists(admin, "rolesInfo", spec.role) else "createRole"
        admin.command(verb, spec.role, privileges=role_privileges(spec, database), roles=[])
    for spec, password in ((READ_ONLY, read_password), (READ_WRITE, write_password)):
        verb = "updateUser" if _exists(admin, "usersInfo", spec.user) else "createUser"
        admin.command(verb, spec.user, pwd=password, roles=[{"role": spec.role, "db": ROLE_DATABASE}])


def _auth_info(uri: str) -> Mapping[str, Any]:
    client: MongoClient[dict[str, Any]] = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        status: Mapping[str, Any] = client[ROLE_DATABASE].command("connectionStatus", showPrivileges=True)
    finally:
        client.close()
    auth_info: Mapping[str, Any] = status["authInfo"]
    return auth_info


def _grants(privileges: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(p.get("resource", {}).get("collection")), str(action))
        for p in privileges
        for action in p.get("actions", [])
    }


def check_auth_info(auth_info: Mapping[str, Any], spec: AccessSpec, database: str) -> None:
    """Raise `PrivilegeError` unless the connection is exactly `spec.user` in `admin` holding exactly
    its role: nothing wider (a policy bug could use it) and nothing missing (calls would fail later)."""
    users = [(u.get("user"), u.get("db")) for u in auth_info.get("authenticatedUsers", [])]
    if users != [(spec.user, ROLE_DATABASE)]:
        raise PrivilegeError(f"the connection must authenticate as {spec.user} in {ROLE_DATABASE} only")
    privileges = list(auth_info.get("authenticatedUserPrivileges", []))
    check_privileges(privileges, spec, database)
    missing = _grants(role_privileges(spec, database)) - _grants(privileges)
    if missing:
        raise PrivilegeError(f"{spec.user} lacks {len(missing)} privileges of its role: re-run provisioning")


def assert_least_privilege(uri: str, spec: AccessSpec, database: str) -> None:
    """Refuse to serve unless `uri` authenticates as exactly `spec.user`, within its role."""
    check_auth_info(_auth_info(uri), spec, database)


def _env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main(env: Mapping[str, str] | None = None) -> None:
    """Provision the users. Env: MCP_MONGO_ADMIN_URI (a user that manages roles and users),
    MCP_MONGO_DATABASE (default `onboarding`), MCP_MONGO_READ_PASSWORD, MCP_MONGO_WRITE_PASSWORD."""
    source = os.environ if env is None else env
    client: MongoClient[dict[str, Any]] = MongoClient(_env(source, "MCP_MONGO_ADMIN_URI"))
    try:
        provision(
            client[ROLE_DATABASE],
            source.get("MCP_MONGO_DATABASE") or DEFAULT_DATABASE,
            read_password=_env(source, "MCP_MONGO_READ_PASSWORD"),
            write_password=_env(source, "MCP_MONGO_WRITE_PASSWORD"),
        )
    finally:
        client.close()
    sys.stdout.write(f"provisioned {READ_ONLY.user} and {READ_WRITE.user}\n")


if __name__ == "__main__":  # pragma: no cover
    main()
