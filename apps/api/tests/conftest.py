"""API test fixtures: a client on a fresh app, with no telemetry left over from another test."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_telemetry.testing import reset_telemetry_globals

__all__ = ["app", "client"]


@pytest.fixture
def app() -> FastAPI:
    reset_telemetry_globals()
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions stays on (the default): an unhandled error must become a response,
    # never escape the app.
    with TestClient(app) as test_client:
        yield test_client
