"""Guards for the CI gate (goal F-3, PRD §12 Quality, §8.2; ADR docs/adr/0001-ci-host.md).

The workflow is checked as data: what runs, in which order, on which events, with which pinned
actions. A hosted run is the only proof that it is green; these tests stop the file from drifting.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ADR = ROOT / "docs" / "adr" / "0001-ci-host.md"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
CHECK_JOB, INT_JOB = "check", "integration"


def _workflow() -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _jobs() -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], _workflow()["jobs"])


def _run_lines(job: str) -> list[str]:
    return [str(step["run"]).strip() for step in _jobs()[job]["steps"] if "run" in step]


def _uses(job: str) -> list[str]:
    return [str(step["uses"]) for step in _jobs()[job]["steps"] if "uses" in step]


def _triggers() -> dict[str, Any]:
    wf = _workflow()
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return cast(dict[str, Any], wf.get("on", wf.get(True)))


def test_workflow_runs_on_every_push_and_on_pull_requests_to_main() -> None:
    triggers = _triggers()
    assert "push" in triggers
    push = triggers["push"] or {}
    assert "branches" not in push, "every push must run CI, not only some branches"
    assert "paths" not in push
    assert "paths-ignore" not in push
    assert triggers["pull_request"]["branches"] == ["main"]


def test_workflow_token_is_read_only() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_superseded_runs_on_the_same_ref_are_cancelled() -> None:
    concurrency = _workflow()["concurrency"]
    assert "github.ref" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is True


def test_workflow_has_exactly_the_check_and_integration_jobs() -> None:
    assert set(_jobs()) == {CHECK_JOB, INT_JOB}


def test_check_job_bootstraps_then_runs_make_check() -> None:
    assert _run_lines(CHECK_JOB) == ["make bootstrap", "make check"]


def test_integration_job_runs_after_check_and_runs_make_test_int() -> None:
    job = _jobs()[INT_JOB]
    assert job["needs"] == CHECK_JOB
    assert _run_lines(INT_JOB) == ["make bootstrap", "make test-int"]


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_job_runs_on_a_pinned_linux_runner_with_a_timeout(job: str) -> None:
    # testcontainers needs a Docker daemon: GitHub's hosted Ubuntu runners provide one.
    assert re.fullmatch(r"ubuntu-\d\d\.\d\d", _jobs()[job]["runs-on"])
    assert 0 < int(_jobs()[job]["timeout-minutes"]) <= 30


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_every_action_is_pinned_to_a_commit_sha(job: str) -> None:
    uses = _uses(job)
    assert uses, f"{job} uses no actions"
    unpinned = [u for u in uses if not SHA_PIN.match(u)]
    assert not unpinned, f"actions must be pinned to a full commit SHA: {unpinned}"


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_checkout_does_not_persist_the_token(job: str) -> None:
    steps = [s for s in _jobs()[job]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(steps) == 1
    assert steps[0]["with"]["persist-credentials"] is False


def _step(job: str, action: str) -> dict[str, Any]:
    matches = [s for s in _jobs()[job]["steps"] if str(s.get("uses", "")).startswith(f"{action}@")]
    assert len(matches) == 1, f"{job} must use {action} exactly once"
    return cast(dict[str, Any], matches[0])


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_uv_is_cached_and_matches_the_pinned_version(job: str) -> None:
    with_ = _step(job, "astral-sh/setup-uv")["with"]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required = pyproject["tool"]["uv"]["required-version"]  # e.g. ">=0.11.6,<0.12"
    bounds = re.fullmatch(r">=([\d.]+),<([\d.]+)", required)
    assert bounds is not None, f"unexpected uv required-version {required!r}"
    low, high = (_version(v) for v in bounds.groups())
    assert low <= _version(str(with_["version"])) < high
    assert with_["enable-cache"] is True
    assert with_["cache-dependency-glob"] == "uv.lock"


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_node_comes_from_nvmrc_with_the_npm_cache(job: str) -> None:
    with_ = _step(job, "actions/setup-node")["with"]
    assert with_["node-version-file"] == ".nvmrc"
    assert with_["cache"] == "npm"


@pytest.mark.parametrize("job", [CHECK_JOB, INT_JOB])
def test_nx_cache_is_restored_and_keyed_on_lockfiles(job: str) -> None:
    with_ = _step(job, "actions/cache")["with"]
    assert with_["path"] == ".nx/cache"
    for lockfile in ("uv.lock", "package-lock.json"):
        assert lockfile in with_["key"]
    assert "github.sha" in with_["key"], "a fresh key per commit, so the cache is saved each run"
    assert with_["restore-keys"].strip(), "restore-keys let a new commit reuse the last cache"


def test_adr_records_github_actions_as_the_ci_host_and_closes_dec_5() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "**Status:** accepted" in text
    assert "GitHub Actions" in text
    assert "DEC-5" in text
    assert "## Decision" in text
    assert "## Why" in text


def test_adr_names_the_required_status_checks_as_the_job_names() -> None:
    # Branch protection matches checks by job name; renaming a job silently drops the gate.
    text = ADR.read_text(encoding="utf-8")
    for job in (CHECK_JOB, INT_JOB):
        assert f'"{job}"' in text, f"ADR must list the required check {job!r}"
