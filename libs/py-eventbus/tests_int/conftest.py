"""Connects the event bus integration tests to the compose Mongo replica set (F-2, T-1 pattern).

Mongo publishes no port (PRD §7.11), so a short-lived socat container on the stack's network is
published on 127.0.0.1 at a random port and removed when the session ends. Each test gets its own
scratch database, dropped afterwards.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data.safety_net import TenantCommandListener, install_safety_net
from pymongo import MongoClient
from pymongo.database import Database

ROOT = Path(__file__).resolve().parents[3]
# Pinned by digest, as in libs/py-data/tests_int: this container carries the root login across the tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
TUNNEL_LABEL_KEY = "onb.test.eventbus-mongo-tunnel"

Client = MongoClient[dict[str, object]]


# --- T-2 tenant safety net (PRD §7.9), the same hooks as libs/py-data/tests_int/conftest.py -------


def pytest_configure(config: pytest.Config) -> None:
    """One global command listener, registered before any client exists."""
    install_safety_net()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Unscoped commands sent after the last test's guard fail the run too."""
    leftover = install_safety_net().drain()
    if leftover:
        lines = "\n".join(v.describe() for v in leftover)
        session.config.get_terminal_writer().line(f"tenant safety net, after the last test:\n{lines}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def tenant_safety_net() -> TenantCommandListener:
    return install_safety_net()


@pytest.fixture(autouse=True)
def tenant_safety_net_guard(tenant_safety_net: TenantCommandListener) -> Iterator[None]:
    """Fails the test if any unscoped command on a tenant collection was sent. The worker queue's
    claim and leased writes are allowed by shape (ADR 0002); root read-backs use `unscoped(reason)`."""
    with tenant_safety_net.guard():
        yield


@pytest.fixture
def unscoped(tenant_safety_net: TenantCommandListener) -> Callable[[str], AbstractContextManager[None]]:
    """`with unscoped("why"):` for a root read-back across orgs or a deliberately broken job."""
    return tenant_safety_net.allowing_unscoped


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
    """A fresh database with the bus collections created up front, dropped afterwards."""
    name = f"it_evt1_{uuid.uuid4().hex[:12]}"
    db = root_client[name]
    for collection in ("events", "jobs", "dead_letters", "onboarding_runs", "hires"):
        db.create_collection(collection)
    try:
        yield Scratch(mongo_uri, name, db, tenant_safety_net)
    finally:
        root_client.drop_database(name)
