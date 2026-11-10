"""Every integration suite that talks to MongoDB runs under the T-2 tenant safety net (PRD §7.9).

T-2's box "the listener is on for every integration test" must keep holding as goals add suites, so
this check runs in `make check`: each project with a `test-int` target whose `tests_int` imports
pymongo or `onb_data` must load `onb_data.safety_net_pytest` in its conftest (all five names) and
carry a fixture test asserting every collected test uses the guard. Reads files only.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "onb_data.safety_net_pytest"
REQUIRED_NAMES = frozenset(
    {"pytest_configure", "pytest_sessionfinish", "tenant_safety_net", "tenant_safety_net_guard", "unscoped"}
)
MONGO_MARKERS = ("pymongo", "onb_data")
KNOWN_MONGO_SUITES = {"libs/py-data", "libs/py-eventbus", "libs/py-crypto", "libs/py-telemetry"}


def integration_suites(root: Path) -> dict[str, Path]:
    """{project dir: its tests_int dir} for every project with a `test-int` target."""
    suites: dict[str, Path] = {}
    for project_json in sorted([*root.glob("apps/*/project.json"), *root.glob("libs/*/project.json")]):
        targets = json.loads(project_json.read_text()).get("targets", {})
        suite = project_json.parent / "tests_int"
        if "test-int" in targets and suite.is_dir():
            suites[project_json.parent.relative_to(root).as_posix()] = suite
    return suites


def talks_to_mongo(suite: Path) -> bool:
    return any(marker in path.read_text() for path in suite.glob("*.py") for marker in MONGO_MARKERS)


def missing_plugin_names(suite: Path) -> set[str]:
    """The safety-net names the suite's conftest does not import from the shared plugin."""
    conftest = suite / "conftest.py"
    if not conftest.is_file():
        return set(REQUIRED_NAMES)
    imported = {
        alias.name
        for node in ast.walk(ast.parse(conftest.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module == PLUGIN
        for alias in node.names
    }
    return set(REQUIRED_NAMES - imported)


def has_guard_fixture_test(suite: Path) -> bool:
    """A test file that checks every collected item uses the guard fixture."""
    return any(
        "session.items" in text and ("GUARD_FIXTURE" in text or "tenant_safety_net_guard" in text)
        for text in (path.read_text() for path in suite.glob("test_*.py"))
    )


def problems(root: Path) -> list[str]:
    found: list[str] = []
    for project, suite in integration_suites(root).items():
        if not talks_to_mongo(suite):
            continue
        missing = missing_plugin_names(suite)
        if missing:
            found.append(f"{project}/tests_int/conftest.py does not import {sorted(missing)} from {PLUGIN}")
        if not has_guard_fixture_test(suite):
            found.append(f"{project}/tests_int has no fixture test asserting every test uses the safety net guard")
    return found


def test_every_mongo_integration_suite_runs_under_the_safety_net() -> None:
    assert problems(ROOT) == []


def test_the_check_sees_the_known_mongo_suites() -> None:
    """Guards the detector itself: if it found nothing, the check above would pass vacuously."""
    mongo = {project for project, suite in integration_suites(ROOT).items() if talks_to_mongo(suite)}
    assert mongo >= KNOWN_MONGO_SUITES


def _suite(root: Path, conftest: str | None, test: str) -> Path:
    project = root / "libs" / "py-new"
    (project / "tests_int").mkdir(parents=True)
    (project / "project.json").write_text(json.dumps({"targets": {"test-int": {}}}))
    if conftest is not None:
        (project / "tests_int" / "conftest.py").write_text(conftest)
    (project / "tests_int" / "test_thing.py").write_text(test)
    return project


def test_a_mongo_suite_without_the_plugin_is_reported(tmp_path: Path) -> None:
    _suite(tmp_path, None, "from pymongo import MongoClient\n")
    assert problems(tmp_path) == [
        f"libs/py-new/tests_int/conftest.py does not import {sorted(REQUIRED_NAMES)} from {PLUGIN}",
        "libs/py-new/tests_int has no fixture test asserting every test uses the safety net guard",
    ]


def test_a_partial_import_is_reported(tmp_path: Path) -> None:
    conftest = f"from {PLUGIN} import pytest_configure, tenant_safety_net\n"
    guard_test = "from onb_data import x\n# session.items GUARD_FIXTURE\n"
    _suite(tmp_path, conftest, guard_test)
    assert problems(tmp_path) == [
        "libs/py-new/tests_int/conftest.py does not import "
        "['pytest_sessionfinish', 'tenant_safety_net_guard', 'unscoped'] from onb_data.safety_net_pytest"
    ]


def test_a_complete_suite_passes_and_a_suite_without_mongo_needs_nothing(tmp_path: Path) -> None:
    conftest = f"from {PLUGIN} import {', '.join(sorted(REQUIRED_NAMES))}\n"
    _suite(tmp_path, conftest, "import pymongo\n# session.items tenant_safety_net_guard\n")
    assert problems(tmp_path) == []
    other = tmp_path / "other"
    _suite(other, None, "import httpx\n")
    assert problems(other) == []
