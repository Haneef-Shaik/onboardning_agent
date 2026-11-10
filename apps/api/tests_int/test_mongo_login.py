"""Login and `/me` against a real MongoDB through `MongoUserStore` (AUTH-1; PRD §7.8, §7.9).

Each test uses a scratch database on the compose Mongo, dropped afterwards. Users of two orgs are
added through the store, then everything is read back with the root client too, so the test does
not trust the store to report on itself. Passwords are random per run.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.passwords import hash_password
from onb_api.auth.settings import AuthSettings
from onb_api.auth.users import EmailTakenError, NewUser
from onb_contracts.auth import Role
from onb_data import DataStore, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, object]]
# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root read-back across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]
SETTINGS = AuthSettings(jwt_secret=secrets.token_urlsafe(48), bcrypt_rounds=4)
PASSWORD = secrets.token_urlsafe(12)
OTHER_HASH = hash_password(secrets.token_urlsafe(12), rounds=4)
USERS = [
    ("org_acme", "acme.hr@example.com", "Sameer Khan", Role.HR),
    ("org_globex", "globex.mgr@example.com", "Tom Becker", Role.MANAGER),
]


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


async def _seed(store: DataStore) -> dict[str, str]:
    users = MongoUserStore(store)
    await users.ensure_indexes()
    ids: dict[str, str] = {}
    password_hash = hash_password(PASSWORD, rounds=SETTINGS.bcrypt_rounds)
    for org_id, email, name, role in USERS:
        with tenant_scope(org_id):
            record = await users.add_user(NewUser(email=email, name=name, role=role, password_hash=password_hash))
        ids[email] = record.user_id
    return ids


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_auth1_{uuid.uuid4().hex[:12]}"
    yield _uri(root_client), name
    root_client.drop_database(name)


@pytest.fixture
def seeded(scratch: tuple[str, str]) -> dict[str, str]:
    async def main() -> dict[str, str]:
        store = connect(*scratch)
        try:
            return await _seed(store)
        finally:
            await store.close()

    return asyncio.run(main())


@pytest.fixture
def client(scratch: tuple[str, str], seeded: dict[str, str]) -> Iterator[TestClient]:
    store = connect(*scratch)
    with TestClient(create_app(auth=SETTINGS, users=MongoUserStore(store))) as test_client:
        yield test_client


def test_the_directory_holds_only_the_email_hash_org_and_user(
    root_client: Client, scratch: tuple[str, str], seeded: dict[str, str]
) -> None:
    entries = list(root_client[scratch[1]]["user_logins"].find())
    assert {tuple(sorted(e)) for e in entries} == {("_id", "org_id", "user_id")}
    expected = {hashlib.sha256(email.encode()).hexdigest() for _, email, _, _ in USERS}
    assert {e["_id"] for e in entries} == expected
    assert all("@" not in str(value) for e in entries for value in e.values())


def test_user_records_are_tenant_documents(
    root_client: Client, scratch: tuple[str, str], seeded: dict[str, str], unscoped: Unscoped
) -> None:
    with unscoped("root read-back of both orgs' user records proves each carries its own org_id"):
        docs = {d["email"]: d for d in root_client[scratch[1]]["users"].find()}
    assert {email: d["org_id"] for email, d in docs.items()} == {
        "acme.hr@example.com": "org_acme",
        "globex.mgr@example.com": "org_globex",
    }
    assert all(str(d["password_hash"]).startswith("$2b$04$") for d in docs.values())
    assert all(PASSWORD not in str(d) for d in docs.values())


@pytest.mark.parametrize(("org_id", "email", "name", "role"), USERS)
def test_login_and_me_for_each_org(client: TestClient, org_id: str, email: str, name: str, role: Role) -> None:
    login = client.post("/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["data"]
    assert (me["org_id"], me["email"], me["name"], me["role"]) == (org_id, email, name, role.value)


def test_a_wrong_password_and_an_unknown_email_are_401(client: TestClient) -> None:
    wrong = client.post("/auth/login", json={"email": "acme.hr@example.com", "password": PASSWORD + "x"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert (wrong.status_code, unknown.status_code) == (401, 401)


def test_an_email_is_unique_across_orgs(scratch: tuple[str, str], seeded: dict[str, str]) -> None:
    async def main() -> None:
        store = connect(*scratch)
        try:
            with tenant_scope("org_globex"), pytest.raises(EmailTakenError):
                await MongoUserStore(store).add_user(
                    NewUser(email=" Acme.HR@example.com", name="Dup", role=Role.HR, password_hash=OTHER_HASH)
                )
        finally:
            await store.close()

    asyncio.run(main())


def test_a_user_of_another_org_is_not_found_in_this_orgs_scope(
    scratch: tuple[str, str], seeded: dict[str, str]
) -> None:
    async def main() -> None:
        store = connect(*scratch)
        try:
            users = MongoUserStore(store)
            with tenant_scope("org_acme"):
                assert await users.get_user(seeded["globex.mgr@example.com"]) is None
                assert await users.get_user(seeded["acme.hr@example.com"]) is not None
        finally:
            await store.close()

    asyncio.run(main())


def test_a_failed_user_insert_removes_the_directory_entry(
    root_client: Client, scratch: tuple[str, str], seeded: dict[str, str]
) -> None:
    """Same `_id` as an existing acme user: the user insert fails, and the new login entry is undone."""

    async def main() -> None:
        store = connect(*scratch)
        try:
            with tenant_scope("org_acme"), pytest.raises(Exception, match="duplicate key"):
                await MongoUserStore(store).add_user(
                    NewUser(email="new.person@example.com", name="New", role=Role.HR, password_hash=OTHER_HASH),
                    user_id=seeded["acme.hr@example.com"],
                )
        finally:
            await store.close()

    asyncio.run(main())
    key = hashlib.sha256(b"new.person@example.com").hexdigest()
    assert root_client[scratch[1]]["user_logins"].find_one({"_id": key}) is None
