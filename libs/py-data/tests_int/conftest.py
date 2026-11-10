"""Connects integration tests to the compose Mongo (F-2).

Mongo publishes no port (PRD §7.11), so the fixture opens a short-lived loopback tunnel: a socat
container on the default bridge, joined to the stack's private network, published on 127.0.0.1 at a
random port. It is removed when the session ends. This plays the role of the PRD's SSH tunnel.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[3]
SOCAT_IMAGE = "alpine/socat:1.8.1.3"


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run integration tests with `make test-int` (it loads .env)")
    return value


def _mongo_network() -> str:
    container = _run("docker", "compose", "ps", "-q", "mongo")
    if not container:
        pytest.fail("the compose mongo service is not running: run `make up`")
    networks = _run("docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", container)
    return networks.split()[0]


@pytest.fixture(scope="session")
def mongo_tunnel() -> Iterator[str]:
    network = _mongo_network()
    tunnel = _run(
        "docker",
        "run",
        "-d",
        "--rm",
        "-p",
        "127.0.0.1::27017",
        SOCAT_IMAGE,
        "TCP-LISTEN:27017,fork,reuseaddr",
        "TCP:mongo:27017",
    )
    try:
        _run("docker", "network", "connect", network, tunnel)
        host_port = _run("docker", "port", tunnel, "27017/tcp").splitlines()[0]
        yield host_port
    finally:
        subprocess.run(["docker", "rm", "-f", tunnel], capture_output=True, check=False)


@pytest.fixture(scope="session")
def root_client(mongo_tunnel: str) -> Iterator[MongoClient[dict[str, object]]]:
    user = quote_plus(_required_env("MONGO_ROOT_USERNAME"))
    password = quote_plus(_required_env("MONGO_ROOT_PASSWORD"))
    uri = f"mongodb://{user}:{password}@{mongo_tunnel}/?authSource=admin&directConnection=true"
    client: MongoClient[dict[str, object]] = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def anonymous_client(mongo_tunnel: str) -> Iterator[MongoClient[dict[str, object]]]:
    client: MongoClient[dict[str, object]] = MongoClient(
        f"mongodb://{mongo_tunnel}/?directConnection=true", serverSelectionTimeoutMS=10_000
    )
    try:
        yield client
    finally:
        client.close()
