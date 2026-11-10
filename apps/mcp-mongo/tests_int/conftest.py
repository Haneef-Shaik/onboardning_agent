"""mcp-mongo served by uvicorn on loopback, backed by a throw-away testcontainers MongoDB (PRD §7.5).

The container uses the same pinned image as the compose stack. Its root login is generated per
session and never written anywhere. The server runs in a background thread with telemetry going to
an in-memory exporter, so tests can inspect every span the real process produced.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import uvicorn
from onb_data import connect
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)
from onb_mcp_mongo.server import build_server
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from testcontainers.community.mongodb import MongoDbContainer

# Same image and digest as infra/docker-compose.yml.
MONGO_IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
DATABASE = "onb_mcp_int"
CONTEXT_KEY = secrets.token_urlsafe(48)
RootClient = MongoClient[dict[str, object]]

# Re-exported so pytest registers the safety net hooks and fixtures for this suite.
__all__ = ["pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"]


@dataclass(frozen=True)
class McpServer:
    url: str
    key: str
    telemetry: CapturedTelemetry


def _wait_for_ping(client: RootClient, deadline_s: float = 60) -> None:
    """The image's init step starts a temporary mongod first; wait for the real one to answer."""
    end = time.monotonic() + deadline_s
    while True:
        try:
            client.admin.command("ping")
            return
        except PyMongoError:
            if time.monotonic() > end:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    container = MongoDbContainer(MONGO_IMAGE, username="root", password=secrets.token_hex(16))
    with container:
        uri = container.get_connection_url()
        client: RootClient = MongoClient(uri, serverSelectionTimeoutMS=5_000)
        try:
            _wait_for_ping(client)
        finally:
            client.close()
        yield uri


@pytest.fixture(scope="session")
def root_db(mongo_uri: str) -> Iterator[object]:
    client: RootClient = MongoClient(mongo_uri)
    try:
        yield client[DATABASE]
    finally:
        client.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def mcp_server(mongo_uri: str) -> Iterator[McpServer]:
    with captured_telemetry("mcp-mongo", instrument=True) as telemetry:
        port = _free_port()
        store = connect(mongo_uri, DATABASE, app_name="mcp-mongo")
        server = build_server(store, CONTEXT_KEY, port=port)
        config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
        uv_server = uvicorn.Server(config)
        thread = threading.Thread(target=uv_server.run, daemon=True)
        thread.start()
        end = time.monotonic() + 15
        while not uv_server.started:
            if time.monotonic() > end or not thread.is_alive():
                pytest.fail("mcp-mongo did not start")
            time.sleep(0.05)
        try:
            yield McpServer(f"http://127.0.0.1:{port}/mcp", CONTEXT_KEY, telemetry)
        finally:
            uv_server.should_exit = True
            thread.join(timeout=10)
