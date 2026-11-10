"""Reach the compose collector, Jaeger and Mongo from host-side tests (F-2's tunnel pattern).

None of those services publishes a port (PRD §7.11), so each fixture opens a short-lived loopback
tunnel: a socat container on the default bridge, joined to the stack's private network, published
on 127.0.0.1 at a random port, and removed when the session ends.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): this suite opens a pymongo client
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)

# Re-exported so pytest registers the safety net hooks and fixtures for this suite.
__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]

ROOT = Path(__file__).resolve().parents[3]
# Same digest-pinned image F-2 uses for its Mongo tunnel.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
# Label value = the stack's network, so cleanup never touches another compose project's tunnels.
TUNNEL_LABEL_KEY = "onb.test.telemetry-tunnel"


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run integration tests with `make test-int` (it loads .env)")
    return value


def _endpoint(service: str) -> tuple[str, str]:
    """(network, ip) of a compose service. Tunnels target the IP, so they need no container DNS."""
    container = _run("docker", "compose", "ps", "-q", service)
    if not container:
        pytest.fail(f"the compose {service} service is not running: run `make up`")
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{end}}"
    network, ip = _run("docker", "inspect", "-f", fmt, container).split()
    return network, ip


class Tunnels:
    def __init__(self) -> None:
        self._containers: list[str] = []
        self._ports: dict[tuple[str, int], str] = {}
        self._label = f"{TUNNEL_LABEL_KEY}={_endpoint('otel-collector')[0]}"
        stale = _run("docker", "ps", "-aq", "--filter", f"label={self._label}").split()
        if stale:  # left by a killed session whose cleanup never ran
            subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, check=False)

    def open(self, service: str, port: int) -> str:
        """`127.0.0.1:<random port>` forwarding to `service:port` on the stack's private network."""
        if (service, port) in self._ports:
            return self._ports[(service, port)]
        network, ip = _endpoint(service)
        tunnel = _run(
            "docker",
            "run",
            "-d",
            "--rm",
            "--label",
            self._label,
            "-p",
            f"127.0.0.1::{port}",
            SOCAT_IMAGE,
            f"TCP-LISTEN:{port},fork,reuseaddr",
            f"TCP:{ip}:{port}",
        )
        self._containers.append(tunnel)
        _run("docker", "network", "connect", network, tunnel)
        host_port = _run("docker", "port", tunnel, f"{port}/tcp").splitlines()[0]
        self._ports[(service, port)] = host_port
        return host_port

    def close(self) -> None:
        if self._containers:
            subprocess.run(["docker", "rm", "-f", *self._containers], capture_output=True, check=False)


@pytest.fixture(scope="session")
def tunnels() -> Iterator[Tunnels]:
    opened = Tunnels()
    try:
        yield opened
    finally:
        opened.close()
