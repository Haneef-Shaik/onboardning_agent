"""Connects integration tests to the compose Mongo (F-2).

Mongo publishes no port (PRD §7.11), so the fixture opens a short-lived loopback tunnel: a socat
container on the default bridge, joined to the stack's private network, published on 127.0.0.1 at a
random port. It is removed when the session ends. This plays the role of the PRD's SSH tunnel.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from onb_data.safety_net import TenantCommandListener, install_safety_net
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[3]
# Pinned by digest: this container carries the root login across the tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
# Label value = the stack's network, so cleanup never touches another compose project's tunnel.
TUNNEL_LABEL_KEY = "onb.test.mongo-tunnel"


def pytest_configure(config: pytest.Config) -> None:
    """T-2 safety net (PRD §7.9): one global command listener, registered before any client exists,
    so every client any test builds (repository store, root, raw, leaked) reports to it."""
    install_safety_net()


@pytest.fixture(scope="session")
def tenant_safety_net() -> TenantCommandListener:
    return install_safety_net()


@pytest.fixture(autouse=True)
def tenant_safety_net_guard(tenant_safety_net: TenantCommandListener) -> Iterator[None]:
    """Fails the test (at setup or teardown) if any unscoped command on a tenant collection was sent.

    A test that sends one on purpose drains it (`tenant_safety_net.drain()` or `.check()`); a test's
    deliberate root read-back wraps itself in `unscoped(reason)`.
    """
    with tenant_safety_net.guard():
        yield


@pytest.fixture
def unscoped(tenant_safety_net: TenantCommandListener) -> Callable[[str], AbstractContextManager[None]]:
    """`with unscoped("why"):` lets a test read back as root across orgs to check isolation."""
    return tenant_safety_net.allowing_unscoped


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run integration tests with `make test-int` (it loads .env)")
    return value


def _mongo_endpoint() -> tuple[str, str]:
    """(network, ip) of the compose mongo container. The tunnel targets the IP, so it needs no
    container DNS (which a default-bridge container may not get on Linux after `network connect`)."""
    container = _run("docker", "compose", "ps", "-q", "mongo")
    if not container:
        pytest.fail("the compose mongo service is not running: run `make up`")
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{end}}"
    network, ip = _run("docker", "inspect", "-f", fmt, container).split()
    return network, ip


def _remove_stale_tunnels(label: str) -> None:
    """This stack's tunnels left by a killed test session (their `finally` never ran)."""
    stale = _run("docker", "ps", "-aq", "--filter", f"label={label}").split()
    if stale:
        subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, check=False)


@pytest.fixture(scope="session")
def mongo_tunnel() -> Iterator[str]:
    network, mongo_ip = _mongo_endpoint()
    label = f"{TUNNEL_LABEL_KEY}={network}"
    _remove_stale_tunnels(label)
    tunnel = _run(
        "docker",
        "run",
        "-d",
        "--rm",
        "--label",
        label,
        "-p",
        "127.0.0.1::27017",
        SOCAT_IMAGE,
        "TCP-LISTEN:27017,fork,reuseaddr",
        f"TCP:{mongo_ip}:27017",
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
