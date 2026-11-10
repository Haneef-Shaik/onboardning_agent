"""Connects the event bus integration tests to the compose Mongo replica set (F-2, T-1 pattern).

Mongo publishes no port (PRD §7.11), so a short-lived socat container on the stack's network is
published on 127.0.0.1 at a random port and removed when the session ends. Each test gets its own
scratch database, dropped afterwards.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from pymongo import MongoClient
from pymongo.database import Database

ROOT = Path(__file__).resolve().parents[3]
# Pinned by digest, as in libs/py-data/tests_int: this container carries the root login across the tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
TUNNEL_LABEL_KEY = "onb.test.eventbus-mongo-tunnel"

Client = MongoClient[dict[str, object]]


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
    uri: str = field(repr=False)  # carries the root password: never in a repr or assertion message
    name: str
    db: Database[dict[str, object]]


@pytest.fixture
def scratch(root_client: Client, mongo_uri: str) -> Iterator[Scratch]:
    """A fresh database with the bus collections created up front, dropped afterwards."""
    name = f"it_evt1_{uuid.uuid4().hex[:12]}"
    db = root_client[name]
    for collection in ("events", "jobs", "dead_letters", "onboarding_runs", "hires"):
        db.create_collection(collection)
    try:
        yield Scratch(mongo_uri, name, db)
    finally:
        root_client.drop_database(name)
