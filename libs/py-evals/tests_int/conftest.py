"""Reuses F-2's compose-Mongo fixtures (`mongo_tunnel`, `root_client`) from libs/py-data/tests_int, so
there is one tunnel implementation. Run with `make test-int` or `make evals-selftest` (both load .env)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest
    pytest_configure,
    pytest_sessionfinish,
    tenant_safety_net,
    tenant_safety_net_guard,
    unscoped,
)

_PY_DATA_CONFTEST = Path(__file__).resolve().parents[2] / "py-data" / "tests_int" / "conftest.py"
_spec = importlib.util.spec_from_file_location("onb_py_data_tests_int_conftest", _PY_DATA_CONFTEST)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load {_PY_DATA_CONFTEST}")
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)

mongo_tunnel = _shared.mongo_tunnel
root_client = _shared.root_client

__all__ = [
    "mongo_tunnel",
    "mongo_uri",
    "pytest_configure",
    "pytest_sessionfinish",
    "root_client",
    "tenant_safety_net",
    "tenant_safety_net_guard",
    "unscoped",
]


@pytest.fixture(scope="session")
def mongo_uri(mongo_tunnel: str) -> str:
    """A URI for the harness: the compose Mongo through the tunnel, as the root user from .env."""
    user = quote_plus(os.environ.get("MONGO_ROOT_USERNAME", ""))
    password = quote_plus(os.environ.get("MONGO_ROOT_PASSWORD", ""))
    if not user or not password:
        pytest.fail("MONGO_ROOT_USERNAME/MONGO_ROOT_PASSWORD are not set: run with `make evals-selftest`")
    return f"mongodb://{user}:{password}@{mongo_tunnel}/?authSource=admin&directConnection=true"
