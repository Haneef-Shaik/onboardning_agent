"""Guards for the Nx monorepo layout (goal F-1, PRD §11 repo layout, §12 Quality).

These tests read the workspace files only; they need no network and no Nx daemon.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PY_APPS = ("api", "orchestrator", "mcp-hub", "mcp-mongo", "mcp-mocks")
TS_APPS = ("web",)
PY_LIBS = (
    "py-contracts",
    "py-data",
    "py-crypto",
    "py-llm",
    "py-agents",
    "py-guardrails",
    "py-telemetry",
    "py-eventbus",
    "py-evals",
)
TS_LIBS = ("ts-ui", "ts-api-client")

PROJECT_DIRS = (
    *(f"apps/{name}" for name in (*PY_APPS, *TS_APPS)),
    *(f"libs/{name}" for name in (*PY_LIBS, *TS_LIBS)),
)
PY_PROJECT_DIRS = (*(f"apps/{n}" for n in PY_APPS), *(f"libs/{n}" for n in PY_LIBS))
REQUIRED_TARGETS = ("lint", "typecheck", "test")
COVERAGE_MIN = 80
EXTRA_FOLDERS = ("config", "templates", "infra", "docs/adr")


def _project(rel: str) -> dict:
    return json.loads((ROOT / rel / "project.json").read_text(encoding="utf-8"))


def _command(target: dict) -> str:
    options = target.get("options", {})
    cmd = options.get("command") or " && ".join(options.get("commands", []))
    return str(cmd)


def test_layout_has_exactly_the_17_prd_projects() -> None:
    assert len(PROJECT_DIRS) == 17
    found = sorted(str(p.parent.relative_to(ROOT)) for p in ROOT.glob("*/*/project.json"))
    assert found == sorted(PROJECT_DIRS)


@pytest.mark.parametrize("rel", PROJECT_DIRS)
def test_project_name_matches_folder_and_has_targets(rel: str) -> None:
    project = _project(rel)
    assert project["name"] == Path(rel).name
    for target in REQUIRED_TARGETS:
        assert target in project.get("targets", {}), f"{rel} has no '{target}' target"


@pytest.mark.parametrize("rel", PROJECT_DIRS)
def test_project_has_a_test(rel: str) -> None:
    tests = [*(ROOT / rel).rglob("test_*.py"), *(ROOT / rel).rglob("*.test.ts*")]
    tests = [t for t in tests if "node_modules" not in t.parts]
    assert tests, f"{rel} has no test file"


@pytest.mark.parametrize("rel", [f"libs/{n}" for n in PY_LIBS])
def test_python_lib_enforces_80_percent_line_coverage(rel: str) -> None:
    cmd = _command(_project(rel)["targets"]["test"])
    match = re.search(r"--cov-fail-under[= ](\d+)", cmd)
    assert match, f"{rel} test target does not enforce a coverage threshold: {cmd}"
    assert int(match.group(1)) >= COVERAGE_MIN
    package = _package_name(rel)
    assert f"--cov={package}" in cmd, f"{rel} measures the wrong package: {cmd}"


def _package_name(rel: str) -> str:
    data = tomllib.loads((ROOT / rel / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["name"]).replace("-", "_")


def test_root_uv_workspace_covers_every_python_project() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = data["tool"]["uv"]["workspace"]["members"]
    assert sorted(members) == sorted(PY_PROJECT_DIRS)
    assert data["project"]["requires-python"].startswith(">=3.12")


@pytest.mark.parametrize("rel", PY_PROJECT_DIRS)
def test_python_project_has_no_own_virtualenv_or_poetry(rel: str) -> None:
    assert not (ROOT / rel / ".venv").exists()
    assert not (ROOT / rel / "poetry.lock").exists()
    assert not (ROOT / rel / "uv.lock").exists()
    data = tomllib.loads((ROOT / rel / "pyproject.toml").read_text(encoding="utf-8"))
    assert "poetry" not in data.get("tool", {})


def test_mypy_is_strict() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["tool"]["mypy"]["strict"] is True


def test_nx_python_plugin_is_pinned_to_uv() -> None:
    nx = json.loads((ROOT / "nx.json").read_text(encoding="utf-8"))
    plugins = [p for p in nx.get("plugins", []) if isinstance(p, dict)]
    nxlv = [p for p in plugins if p.get("plugin") == "@nxlv/python"]
    assert nxlv and nxlv[0]["options"]["packageManager"] == "uv"


@pytest.mark.parametrize("rel", EXTRA_FOLDERS)
def test_extra_folders_exist(rel: str) -> None:
    assert (ROOT / rel).is_dir()


def test_makefile_check_runs_plan_check_and_all_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("bootstrap:", "check:", "test-int:", "fmt:"):
        assert re.search(rf"^{re.escape(target)}", makefile, re.M), f"Makefile lacks {target}"
    assert "scripts/plan/plan.py check" in makefile
    for target in REQUIRED_TARGETS:
        assert target in makefile


@pytest.mark.parametrize("path", [".env", ".env.local", "evals/reports/x.json", ".venv/x", "node_modules/x", "infra/mongo/keyfile"])
def test_gitignore_covers(path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", path], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.stdout.strip() == path, f"{path} is not ignored"


@pytest.mark.parametrize("path", [".env.example", "evals/datasets/onboarding_agents_v1.jsonl"])
def test_gitignore_keeps(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "--no-index", path], cwd=ROOT, capture_output=True, check=False)
    assert result.returncode == 1, f"{path} must not be ignored"
