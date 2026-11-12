"""`make test-pii` (SEC-1) on the compose stack: MongoDB replica set, OTel collector and Jaeger.

None of them publishes a port (PRD §7.11), so short-lived loopback tunnels (socat containers on the stack's
network, the F-2 pattern) carry the root login, the OTLP export and the Jaeger query. The platform itself
runs in this process (`stack.py`) over a scratch database dropped at the end, under the T-2 tenant safety
net; a deliberate root read across collections says why in `unscoped("reason")`.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)
from onb_telemetry import TelemetryHandle, TelemetrySettings, init_telemetry
from pymongo import MongoClient

from .collect import JsonLogFormatter
from .fixture import PiiFixture
from .platform import ORG, ROOT, LlmInputs, install_llm_capture
from .stack import Platform, running_platform

__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]

# Same digest-pinned image as the other suites' tunnels.
SOCAT_IMAGE = "alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
TUNNEL_LABEL_KEY = "onb.test.pii-tunnel"  # its own key: another suite's stale-tunnel cleanup never removes these
QUIET_LOGGERS = ("pymongo", "httpcore", "httpx", "asyncio", "urllib3", "mcp.client.streamable_http")
OTEL_VARS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_TRACES_EXPORTER")
RootClient = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]


def _run(*args: str) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set: run the suite with `make test-pii` (it loads .env)")
    return value


def _endpoint(service: str) -> tuple[str, str]:
    container = _run("docker", "compose", "ps", "-q", service)
    if not container:
        pytest.fail(f"the compose {service} service is not running: run `make up`")
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{end}}"
    network, ip = _run("docker", "inspect", "-f", fmt, container).split()
    return network, ip


class Tunnels:
    def __init__(self) -> None:
        self._containers: list[str] = []
        self._label = f"{TUNNEL_LABEL_KEY}={_endpoint('mongo')[0]}"
        stale = _run("docker", "ps", "-aq", "--filter", f"label={self._label}").split()
        if stale:  # left by a killed session
            subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, check=False)

    def open(self, service: str, port: int) -> str:
        network, ip = _endpoint(service)
        tunnel = _run(
            "docker", "run", "-d", "--rm", "--label", self._label, "-p", f"127.0.0.1::{port}",
            SOCAT_IMAGE, f"TCP-LISTEN:{port},fork,reuseaddr", f"TCP:{ip}:{port}",
        )  # fmt: skip
        self._containers.append(tunnel)
        _run("docker", "network", "connect", network, tunnel)
        return _run("docker", "port", tunnel, f"{port}/tcp").splitlines()[0]

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


@dataclass(frozen=True)
class Session:
    """What the scan needs from the session: where things went, and when the session started."""

    mongo_uri: str = field(repr=False)  # carries the root password
    database: str
    jaeger: str
    service: str
    started_us: int
    log_file: Path
    telemetry: TelemetryHandle
    llm: LlmInputs
    report_dir: Path


@pytest.fixture(scope="session")
def session(tunnels: Tunnels, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Session]:
    user = quote_plus(_required_env("MONGO_ROOT_USERNAME"))
    password = quote_plus(_required_env("MONGO_ROOT_PASSWORD"))
    mongo = tunnels.open("mongo", 27017)
    uri = f"mongodb://{user}:{password}@{mongo}/?authSource=admin&directConnection=true"
    saved = {name: os.environ.get(name) for name in OTEL_VARS}
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://{tunnels.open('otel-collector', 4318)}"
    os.environ["OTEL_TRACES_EXPORTER"] = "otlp"
    jaeger = tunnels.open("jaeger", 16686)
    service = f"pii-scan-{secrets.token_hex(4)}"
    started_us = int(time.time() * 1_000_000)
    # Telemetry before any Mongo client (F-4: PyMongo traces only clients made after it), as every main does.
    telemetry = init_telemetry(service, settings=TelemetrySettings.from_env())
    log_file = tmp_path_factory.mktemp("logs") / "app.log"
    handler = _capture_logs(log_file)
    llm = LlmInputs()
    undo = install_llm_capture(llm)
    report_dir = Path(os.environ.get("PII_SCAN_REPORT_DIR") or ROOT / "reports" / "test-pii")
    database = f"pii_{secrets.token_hex(6)}"
    try:
        yield Session(uri, database, jaeger, service, started_us, log_file, telemetry, llm, report_dir)
    finally:
        undo()
        for name, value in saved.items():  # the tamper report's subprocesses must not export here
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        logging.getLogger().removeHandler(handler)
        telemetry.shutdown()
        client: RootClient = MongoClient(uri, serverSelectionTimeoutMS=10_000)
        try:
            client.drop_database(database)
        finally:
            client.close()


def _capture_logs(path: Path) -> logging.Handler:
    """Every log record of every logger, as JSON lines, after the OBS-1 scrubber (it scrubs at record
    creation, so any handler gets the scrubbed record)."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonLogFormatter())
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
    return handler


@pytest.fixture(scope="session")
def fixture_pii() -> PiiFixture:
    return PiiFixture.generate()


@pytest.fixture(scope="session")
def root_client(session: Session) -> Iterator[RootClient]:
    client: RootClient = MongoClient(session.mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def platform(session: Session, root_client: RootClient, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Platform]:
    root_db = root_client[session.database]

    def hire() -> Mapping[str, Any]:
        """What the templates agent's `mongo__find` of the hire returns: its name and mobile token."""
        found = root_db["new_hires"].find_one({"org_id": ORG}, sort=[("created_at", -1)])
        assert found is not None, "no hire yet"
        return {"name": found["name"], "mobile_token": found["pii"]["mobile"]}

    tmp = tmp_path_factory.mktemp("scripts")
    with running_platform(session.mongo_uri, session.database, root_db, tmp, hire) as running:
        yield running
