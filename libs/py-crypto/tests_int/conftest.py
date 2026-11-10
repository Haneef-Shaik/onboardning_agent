"""Reuses F-2's compose-Mongo fixtures (`mongo_tunnel`, `root_client`) from libs/py-data/tests_int, so
there is one tunnel implementation. Run with `make test-int`, which brings the stack up and loads .env."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PY_DATA_CONFTEST = Path(__file__).resolve().parents[2] / "py-data" / "tests_int" / "conftest.py"
_spec = importlib.util.spec_from_file_location("onb_py_data_tests_int_conftest", _PY_DATA_CONFTEST)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load {_PY_DATA_CONFTEST}")
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)

mongo_tunnel = _shared.mongo_tunnel
root_client = _shared.root_client
