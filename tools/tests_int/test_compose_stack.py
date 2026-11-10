"""Live checks on the running compose stack (F-2; PRD §7.11, §7.7, §7.2). Run by `make test-int`,
which brings the stack up first (`make up`). Commands run from the repo root, where `.env` names the
compose file, project and profile.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = {"mongo", "otel-collector", "jaeger", "caddy"}
INTERNAL = SERVICES - {"caddy"}


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, check=check)


@pytest.fixture(scope="module")
def config() -> Mapping[str, Any]:
    parsed: Mapping[str, Any] = json.loads(_compose("config", "--format", "json").stdout)
    return parsed


def _ps() -> list[dict[str, Any]]:
    """`compose ps --format json` rows; older compose prints one array, newer one object per line."""
    out = _compose("ps", "--all", "--format", "json").stdout.strip()
    if out.startswith("["):
        rows: list[dict[str, Any]] = json.loads(out)
        return rows
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_every_service_is_running_and_healthy() -> None:
    rows = {row["Service"]: row for row in _ps()}
    assert set(rows) == SERVICES
    for name, row in rows.items():
        assert row["State"] == "running", f"{name}: {row['State']}"
        assert row["Health"] == "healthy", f"{name}: {row['Health']}"


def test_only_caddy_publishes_ports(config: Mapping[str, Any]) -> None:
    services = config["services"]
    assert set(services) == SERVICES
    with_ports = {name for name, svc in services.items() if svc.get("ports")}
    assert with_ports == {"caddy"}


def test_every_service_has_a_healthcheck_and_restarts(config: Mapping[str, Any]) -> None:
    for name, svc in config["services"].items():
        assert svc.get("healthcheck", {}).get("test"), f"{name} has no healthcheck"
        assert svc.get("restart") == "unless-stopped", f"{name}: restart={svc.get('restart')}"


def test_internal_services_are_only_on_the_private_network(config: Mapping[str, Any]) -> None:
    networks = config["networks"]
    assert networks["backend"].get("internal") is True
    for name in INTERNAL:
        assert set(config["services"][name]["networks"]) == {"backend"}, name
    assert set(config["services"]["caddy"]["networks"]) == {"backend", "edge"}


def test_mongo_data_is_on_a_named_volume(config: Mapping[str, Any]) -> None:
    mounts = config["services"]["mongo"]["volumes"]
    data = [m for m in mounts if m["target"] == "/data/db"]
    assert data
    assert data[0]["type"] == "volume"
    assert data[0]["source"] in config["volumes"]


def _mongosh(script: str, *, auth: bool) -> subprocess.CompletedProcess[str]:
    creds = '-u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" ' if auth else ""
    cmd = f"mongosh --quiet {creds}--eval '{script}'"
    return _compose("exec", "-T", "mongo", "sh", "-c", cmd, check=False)


def test_unauthenticated_mongosh_is_refused() -> None:
    result = _mongosh("db.adminCommand({listDatabases: 1})", auth=False)
    assert result.returncode != 0
    assert "requires authentication" in result.stdout + result.stderr


def test_authenticated_mongosh_sees_a_primary_replica_set() -> None:
    result = _mongosh("const s = rs.status(); print(s.set, s.members[0].stateStr)", auth=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["rs0", "PRIMARY"]


def _jaeger_wget(*args: str) -> subprocess.CompletedProcess[str]:
    return _compose("exec", "-T", "jaeger", "wget", "-q", "-O", "-", *args, check=False)


def test_collector_forwards_traces_to_jaeger() -> None:
    service = f"f2-probe-{int(time.time())}"
    span = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": os.urandom(16).hex(),
                                "spanId": os.urandom(8).hex(),
                                "name": "probe",
                                "kind": 1,
                                "startTimeUnixNano": str(time.time_ns()),
                                "endTimeUnixNano": str(time.time_ns() + 1_000_000),
                            }
                        ]
                    }
                ],
            }
        ]
    }
    sent = _jaeger_wget(
        "--header",
        "Content-Type: application/json",
        "--post-data",
        json.dumps(span),
        "http://otel-collector:4318/v1/traces",
    )
    assert sent.returncode == 0, sent.stderr
    for _ in range(30):
        services = _jaeger_wget("http://localhost:16686/api/v3/services")
        if service in services.stdout:
            return
        time.sleep(1)
    pytest.fail(f"{service} never reached Jaeger: {services.stdout} {services.stderr}")


def _caddy_status(scheme: str, host: str, path: str, *auth: str) -> int:
    """HTTP status from Caddy's published port, as a browser on this machine would see it.

    curl `--resolve` sends the right SNI for HTTPS; `-k` accepts Caddy's internal dev CA;
    `--noproxy` keeps a CI proxy out of a loopback call.
    """
    bind = os.environ.get("CADDY_BIND_ADDRESS") or "127.0.0.1"
    address = "127.0.0.1" if bind == "0.0.0.0" else bind  # noqa: S104 - compares, never binds
    var = "CADDY_HTTPS_PORT" if scheme == "https" else "CADDY_HTTP_PORT"
    port = os.environ.get(var) or ("443" if scheme == "https" else "80")
    result = subprocess.run(
        [
            "curl",
            "-sk",
            "--noproxy",
            "*",
            "--max-time",
            "5",
            "--resolve",
            f"{host}:{port}:{address}",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            *auth,
            f"{scheme}://{host}:{port}{path}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(result.stdout or 0)


def test_caddy_serves_health_on_the_published_port() -> None:
    assert _caddy_status("http", "localhost", "/healthz") == 200
    assert _caddy_status("https", "localhost", "/healthz") == 200


def test_jaeger_through_caddy_requires_basic_auth() -> None:
    assert _caddy_status("https", "jaeger.localhost", "/") == 401
    assert _caddy_status("https", "jaeger.localhost", "/", "-u", "admin:wrong-password") == 401


def test_jaeger_is_not_served_over_plain_http() -> None:
    assert _caddy_status("http", "jaeger.localhost", "/") == 404


def test_unknown_hosts_get_a_404() -> None:
    assert _caddy_status("http", "unknown.example", "/") == 404
