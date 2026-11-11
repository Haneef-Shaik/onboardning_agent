"""`python -m onb_api.seed` against a real MongoDB (SEED-1 boxes 1 and 2; PRD §7.11).

Each test runs the seed command (`main`, exactly what `make seed` runs, with its environment) on a
scratch database of the compose Mongo, dropped afterwards. Everything is read back with the root
client, so the test does not trust the seed to report on itself; the seeded users then log in through
the API. A second run must leave every collection, document and index exactly as the first left it.
Passwords and the master key are random per run.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_agents.registry import load_manifests
from onb_api import create_app
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.settings import AuthSettings
from onb_api.seed.__main__ import main as seed_command
from onb_data import connect
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root read-back across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]
ROOT = Path(__file__).resolve().parents[3]
ARGS = ["--seed", str(ROOT / "config" / "seed.yaml"), "--config", str(ROOT / "config")]
SETTINGS = AuthSettings(jwt_secret=secrets.token_urlsafe(48), bcrypt_rounds=4)
PASSWORDS = {
    "SEED_HR_ADMIN_PASSWORD": secrets.token_urlsafe(18),
    "SEED_HR_PASSWORD": secrets.token_urlsafe(18),
    "SEED_MANAGER_PASSWORD": secrets.token_urlsafe(18),
}
USERS = {
    "u_demo_hradmin": ("hr.admin@demo.example", "HR_ADMIN", "SEED_HR_ADMIN_PASSWORD"),
    "u_demo_hr": ("hr@demo.example", "HR", "SEED_HR_PASSWORD"),
    "u_demo_mgr_priya": ("priya.manager@demo.example", "MANAGER", "SEED_MANAGER_PASSWORD"),
    "u_demo_mgr_arjun": ("arjun.manager@demo.example", "MANAGER", "SEED_MANAGER_PASSWORD"),
}
AGENTS = sorted(m.name for m in load_manifests(ROOT / "config" / "agents"))


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client) -> Iterator[tuple[str, str]]:
    name = f"it_seed1_{uuid.uuid4().hex[:12]}"
    yield _uri(root_client), name
    root_client.drop_database(name)


@pytest.fixture
def env(scratch: tuple[str, str]) -> dict[str, str]:
    master = base64.b64encode(secrets.token_bytes(32)).decode()
    return {**PASSWORDS, "MONGO_URI": scratch[0], "MONGO_DATABASE": scratch[1], "PII_MASTER_KEY": master}


def _seed(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert seed_command(ARGS, env) == 0, capsys.readouterr().err
    return capsys.readouterr().out.splitlines()


def _snapshot(db: Any) -> dict[str, Any]:
    """Every collection of the database: its documents (by `_id`) and its indexes."""
    return {
        name: {
            "documents": sorted(db[name].find(), key=lambda d: str(d["_id"])),
            "indexes": sorted((i["name"], i.get("unique", False)) for i in db[name].list_indexes()),
        }
        for name in sorted(db.list_collection_names())
    }


def test_make_seed_on_an_empty_database_creates_every_listed_record(
    root_client: Client,
    scratch: tuple[str, str],
    env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    unscoped: Unscoped,
) -> None:
    db = root_client[scratch[1]]
    assert db.list_collection_names() == []
    out = _seed(env, capsys)

    with unscoped("root read-back of everything the seed wrote, to check it without trusting the seed"):
        assert list(db["orgs"].find()) == [{"_id": "org_demo", "name": "Demo Org (synthetic)"}]
        keys = list(db["org_keys"].find())
        users = {u["_id"]: u for u in db["users"].find()}
        logins = list(db["user_logins"].find())
        agents = list(db["agent_registry"].find())
        catalog = list(db["tool_catalog"].find())
        heads = list(db["templates"].find())
        versions = list(db["template_versions"].find())
        drafts = list(db["template_drafts"].find())
        audit = list(db["audit_log"].find())

    assert [(k["org_id"], k["key_version"]) for k in keys] == [("org_demo", 1)]
    assert {uid: (u["org_id"], u["email"], u["role"]) for uid, u in users.items()} == {
        uid: ("org_demo", email, role) for uid, (email, role, _) in USERS.items()
    }
    assert all(str(u["password_hash"]).startswith("$2b$12$") for u in users.values())
    assert sorted((e["org_id"], e["user_id"]) for e in logins) == sorted(("org_demo", uid) for uid in USERS)
    assert sorted((a["_id"], a["version"], a["active"]) for a in agents) == [(name, 1, True) for name in AGENTS]
    assert [c["_id"] for c in catalog] == ["tool_catalog"]
    assert [(h["org_id"], h["key"], h["latest_version"]) for h in heads] == [("org_demo", "engineer-india", 1)]
    assert [(v["key"], v["version"], v["published_by"]) for v in versions] == [("engineer-india", 1, "u_demo_hradmin")]
    assert [(d["key"], d["status"], d["published_version"]) for d in drafts] == [("engineer-india", "published", 1)]
    assert {(a["org_id"], a["actor"]["id"]) for a in audit} == {("org_demo", "u_demo_hradmin")}
    published = [(a["target"], a["outcome"]) for a in audit if a["action"] == "template.publish"]
    assert published == [("template:engineer-india/v1", "success")]
    registered = sorted(a["target"] for a in audit if a["action"] == "registry.agent_register")
    assert registered == [f"agent:{name}/v1" for name in AGENTS]

    assert out[0] == "created org:org_demo"
    assert out[-1] == f"{len(out) - 1} created, 0 unchanged"


def test_the_seeded_users_log_in_with_their_passwords_from_the_environment(
    scratch: tuple[str, str], env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(env, capsys)
    store = connect(*scratch)
    with TestClient(create_app(auth=SETTINGS, users=MongoUserStore(store))) as client:
        for user_id, (email, role, password_env) in USERS.items():
            login = client.post("/auth/login", json={"email": email, "password": PASSWORDS[password_env]})
            assert login.status_code == 200, user_id
            token = login.json()["data"]["access_token"]
            me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["data"]
            assert (me["user_id"], me["org_id"], me["role"]) == (user_id, "org_demo", role)
        wrong = client.post("/auth/login", json={"email": "hr@demo.example", "password": "not-the-password"})
        assert wrong.status_code == 401


def test_a_second_make_seed_changes_nothing(
    root_client: Client,
    scratch: tuple[str, str],
    env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    unscoped: Unscoped,
) -> None:
    db = root_client[scratch[1]]
    first = _seed(env, capsys)
    with unscoped("root snapshot of every collection, documents and indexes, before the second run"):
        before = _snapshot(db)
    second = _seed(env, capsys)
    with unscoped("root snapshot of every collection, documents and indexes, after the second run"):
        after = _snapshot(db)

    assert after == before
    assert all(line.startswith("unchanged ") for line in second[:-1])
    assert second[-1] == f"0 created, {len(second) - 1} unchanged"
    assert len(first) == len(second)


def test_a_seed_with_another_master_key_is_refused_and_writes_nothing(
    root_client: Client,
    scratch: tuple[str, str],
    env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    unscoped: Unscoped,
) -> None:
    db = root_client[scratch[1]]
    _seed(env, capsys)
    with unscoped("root snapshot before a seed with the wrong master key"):
        before = _snapshot(db)
    other = {**env, "PII_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode()}
    assert seed_command(ARGS, other) == 1
    err = capsys.readouterr().err
    assert err.startswith("seed refused: ")
    assert other["PII_MASTER_KEY"] not in err
    with unscoped("root snapshot after the refused seed"):
        assert _snapshot(db) == before
