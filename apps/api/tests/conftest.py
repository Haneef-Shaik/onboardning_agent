"""API test fixtures: a client on a fresh app, with no telemetry left over from another test.

The app gets the test auth settings, an in-memory user store seeded with both orgs' users and an
empty in-memory audit log.
`JWT_SECRET` is set for every test (a fresh random value), so tests that build the app from the
environment with `create_app()` start like the real service; the startup tests remove it.
"""

import asyncio
import secrets
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog, InMemoryUserStore, seeded_store
from onb_telemetry.testing import reset_telemetry_globals

__all__ = ["app", "client", "jwt_secret_in_env", "users"]


@pytest.fixture(autouse=True)
def jwt_secret_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))


@pytest.fixture
def users() -> InMemoryUserStore:
    return asyncio.run(seeded_store())


@pytest.fixture
def app(users: InMemoryUserStore) -> FastAPI:
    reset_telemetry_globals()
    return create_app(auth=TEST_SETTINGS, users=users, audit=InMemoryAuditLog())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions stays on (the default): an unhandled error must become a response,
    # never escape the app.
    with TestClient(app) as test_client:
        yield test_client
