"""Connects the notify sink integration tests (NOTIF-1) to the compose Mongo replica set (F-2, T-1 pattern).

Mongo publishes no port (PRD §7.11), so a short-lived socat container on the stack's network is
published on 127.0.0.1 at a random port and removed when the session ends. Each test gets its own
scratch database, dropped afterwards.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data.safety_net import TenantCommandListener
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)
from pymongo import MongoClient
from pymongo.database import Database

ROOT = Path(__file__).resolve().parents[3]
# Pinned by digest, as in libs/py-data/tests_int: this container carries the root login across the tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
TUNNEL_LABEL_KEY = "onb.test.notify-mongo-tunnel"

Client = MongoClient[dict[str, object]]


# Re-exported so pytest registers the safety net hooks and fixtures for this suite.
__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run integration tests with `make test-int` (it loads .env)")
    return value


@pytest.fixture(scope="session")
def mongo_tunnel() -> Iterator[str]:
    container = _run("docker", "compose", "ps", "-q", "mongo")
    if not container:
        pytest.fail("the compose mongo service is not running: run `make up`")
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{end}}"
    network, mongo_ip = _run("docker", "inspect", "-f", fmt, container).split()
    label = f"{TUNNEL_LABEL_KEY}={network}"
    stale = _run("docker", "ps", "-aq", "--filter", f"label={label}").split()
    if stale:
        subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, check=False)
    tunnel = _run(
        "docker", "run", "-d", "--rm", "--label", label, "-p", "127.0.0.1::27017",
        SOCAT_IMAGE, "TCP-LISTEN:27017,fork,reuseaddr", f"TCP:{mongo_ip}:27017",
    )  # fmt: skip
    try:
        _run("docker", "network", "connect", network, tunnel)
        yield _run("docker", "port", tunnel, "27017/tcp").splitlines()[0]
    finally:
        subprocess.run(["docker", "rm", "-f", tunnel], capture_output=True, check=False)


@pytest.fixture(scope="session")
def mongo_uri(mongo_tunnel: str) -> str:
    user = quote_plus(_required_env("MONGO_ROOT_USERNAME"))
    password = quote_plus(_required_env("MONGO_ROOT_PASSWORD"))
    return f"mongodb://{user}:{password}@{mongo_tunnel}/?authSource=admin&directConnection=true"


@pytest.fixture(scope="session")
def root_client(mongo_uri: str) -> Iterator[Client]:
    client: Client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
    try:
        yield client
    finally:
        client.close()


@dataclass(frozen=True)
class Scratch:
    """The scratch database. `db` is the root handle for org-scoped setup; reads across orgs go
    through `find`/`find_one`/`count`, which say so to the T-2 safety net (`unscoped`)."""

    uri: str = field(repr=False)  # carries the root password: never in a repr or assertion message
    name: str
    db: Database[dict[str, object]]
    listener: TenantCommandListener = field(repr=False)

    def root_read(self) -> AbstractContextManager[None]:
        return self.listener.allowing_unscoped("root read-back of the scratch database, across orgs")

    def find(self, collection: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.root_read():
            return list(self.db[collection].find(query or {}))

    def find_one(
        self, collection: str, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        with self.root_read():
            return self.db[collection].find_one(query, projection)

    def count(self, collection: str, query: dict[str, Any] | None = None) -> int:
        with self.root_read():
            return self.db[collection].count_documents(query or {})


@pytest.fixture
def scratch(root_client: Client, mongo_uri: str, tenant_safety_net: TenantCommandListener) -> Iterator[Scratch]:
    """A fresh database with the collections a transaction writes created up front, dropped afterwards."""
    name = f"it_notif1_{uuid.uuid4().hex[:12]}"
    db = root_client[name]
    written = ("events", "jobs", "dead_letters", "mock_outbox", "notifications", "notify_sends", "telegram_callbacks")
    for collection in written:
        db.create_collection(collection)
    try:
        yield Scratch(mongo_uri, name, db, tenant_safety_net)
    finally:
        root_client.drop_database(name)
