"""SEC-1 box 4: CI runs `make test-pii`. The `pii` job of `.github/workflows/ci.yml` generates a runner
`.env` (tools/ci_env.py) and runs the target, which runs the PII suite. Reads files only."""

from __future__ import annotations

import base64
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load_ci_env() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_env", ROOT / "tools" / "ci_env.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ci_env = _load_ci_env()


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    return jobs


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def test_the_ci_workflow_has_a_pii_job_that_runs_make_test_pii_after_check() -> None:
    pii = _jobs()["pii"]
    runs = _runs(pii)
    assert pii["needs"] == "check"
    assert runs.index("make bootstrap") < runs.index("uv run --frozen python tools/ci_env.py > .env")
    assert runs.index("uv run --frozen python tools/ci_env.py > .env") < runs.index("make test-pii")
    assert "continue-on-error" not in pii
    assert all("continue-on-error" not in step for step in pii["steps"])


def test_the_pii_job_cleans_up_whatever_happens() -> None:
    steps = _jobs()["pii"]["steps"]
    down = next(step for step in steps if step.get("run") == "make down")
    assert down["if"] == "always()"


def test_make_test_pii_brings_the_stack_up_and_runs_the_pii_suite() -> None:
    makefile = (ROOT / "Makefile").read_text()
    recipe = re.search(r"^test-pii: up\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert recipe is not None
    assert "pytest tools/tests_pii" in recipe.group(1)
    assert re.search(r"^\.PHONY:.*\btest-pii\b", makefile, re.MULTILINE)


def _parse(text: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in text.splitlines() if re.match(r"^[A-Z][A-Z0-9_]*=", line))
    return dict(pairs)


def test_the_runner_env_fills_every_variable_with_fresh_values() -> None:
    example = (ROOT / ".env.example").read_text()
    first, second = _parse(ci_env.ci_env(example)), _parse(ci_env.ci_env(example))
    assert set(first) == set(_parse(example))
    empty = {name for name, value in first.items() if not value}
    assert empty == set(ci_env.LEFT_EMPTY)
    assert first["MONGO_ROOT_PASSWORD"] != second["MONGO_ROOT_PASSWORD"]
    assert len(base64.b64decode(first["PII_MASTER_KEY"])) == 32
    assert first["JAEGER_BASIC_AUTH_HASH"].startswith("'$2")
    assert (first["CADDY_HTTP_PORT"], first["CADDY_HTTPS_PORT"]) == ("8080", "8443")
    assert first["LLM_CONNECTOR"] == "fake"
