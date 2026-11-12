"""Guards for the CI gate (goal F-3, PRD §12 Quality, §8.2; ADR docs/adr/0001-ci-host.md).

The workflow is checked as data: what runs, in which order, on which events, with which pinned
actions. A hosted run is the only proof that it is green; these tests stop the file from drifting.
"""

from __future__ import annotations

import json
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
CHECK_JOB, INT_JOB, PII_JOB = "check", "integration", "pii"
JOBS = (CHECK_JOB, INT_JOB, PII_JOB)
NX_JOBS = (CHECK_JOB, INT_JOB)  # the pii job runs pytest directly, not through Nx
# The compose stack (`make up`) needs a `.env`; on a runner it is generated per job (SEC-1, tools/ci_env.py).
RUNNER_ENV = "uv run --frozen python tools/ci_env.py > .env"
# Every MASTER-PLAN §7 gate that is a Makefile target runs in CI. `test-xt` runs inside `make check`.
CI_GATES = ("check", "contracts-check", "test-int", "evals-selftest", "test-pii")
NOT_GATES = ("bootstrap", "down")


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
    # Never cancel on main: every commit that lands there must get its own verdict.
    assert concurrency["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"


def _is_gate_step(step: dict[str, Any]) -> bool:
    run = str(step.get("run", "")).strip()
    target = run.removeprefix("make ") if run.startswith("make ") else None
    # `make bootstrap` counts: a conditional install would skip every gate after it. Only `make down` is free.
    return run == RUNNER_ENV or (target is not None and target != "down")


@pytest.mark.parametrize("job", JOBS)
def test_no_job_or_gate_step_can_be_skipped_or_allowed_to_fail(job: str) -> None:
    assert "if" not in _jobs()[job], f"{job}: a condition can silently skip the whole job"
    assert "continue-on-error" not in _jobs()[job], f"{job}: continue-on-error turns red into green"
    for step in _jobs()[job]["steps"]:
        assert "continue-on-error" not in step, f"{job}: continue-on-error turns red into green"
        if "if" in step:
            # Only report and clean-up steps may run regardless of the outcome; they never decide it.
            assert step["if"] == "always()", f"{job}: only `if: always()` is allowed, on {step}"
            assert not _is_gate_step(step), f"{job}: a gate step must not carry a condition: {step}"


def test_workflow_has_exactly_the_check_integration_and_pii_jobs() -> None:
    assert set(_jobs()) == set(JOBS)
    # Required status checks match the job's display name, so it must equal the key.
    assert {key: job["name"] for key, job in _jobs().items()} == {job: job for job in JOBS}


def test_check_job_bootstraps_writes_the_runner_env_then_runs_check_and_contracts() -> None:
    assert _run_lines(CHECK_JOB) == ["make bootstrap", RUNNER_ENV, "make check", "make contracts-check"]


def test_integration_job_runs_after_check_with_the_stack_gates() -> None:
    job = _jobs()[INT_JOB]
    assert job["needs"] == CHECK_JOB
    assert _run_lines(INT_JOB) == ["make bootstrap", RUNNER_ENV, "make test-int", "make evals-selftest"]


def test_pii_job_runs_after_check() -> None:
    assert _jobs()[PII_JOB]["needs"] == CHECK_JOB


def _gate_targets() -> list[str]:
    return [
        str(step["run"]).strip().removeprefix("make ")
        for job in JOBS
        for step in _jobs()[job]["steps"]
        if str(step.get("run", "")).strip().startswith("make ")
        and str(step["run"]).strip().removeprefix("make ") not in NOT_GATES
    ]


def test_every_makefile_gate_runs_in_ci_exactly_once() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    phony = re.search(r"^\.PHONY:(.*)$", makefile, re.MULTILINE)
    assert phony is not None
    for gate in CI_GATES:
        assert gate in phony.group(1).split(), f"Makefile has no {gate!r} target"
    assert sorted(_gate_targets()) == sorted(CI_GATES)


def test_make_check_includes_the_cross_tenant_suite() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    check = re.search(r"^check:(.*)$", makefile, re.MULTILINE)
    assert check is not None
    assert "test-xt" in check.group(1).split()


@pytest.mark.parametrize("job", JOBS)
def test_the_runner_env_is_written_after_bootstrap_and_before_any_gate(job: str) -> None:
    runs = _run_lines(job)
    assert runs.count(RUNNER_ENV) == 1
    env_at = runs.index(RUNNER_ENV)
    assert runs.index("make bootstrap") < env_at
    gates_at = [i for i, run in enumerate(runs) if run.startswith("make ") and run.removeprefix("make ") in CI_GATES]
    assert gates_at
    assert env_at < min(gates_at)


@pytest.mark.parametrize("job", JOBS)
def test_job_runs_on_a_pinned_linux_runner_with_a_timeout(job: str) -> None:
    # testcontainers and the compose stack need a Docker daemon: GitHub's hosted Ubuntu runners provide one.
    assert re.fullmatch(r"ubuntu-\d\d\.\d\d", _jobs()[job]["runs-on"])
    assert 0 < int(_jobs()[job]["timeout-minutes"]) <= 45


@pytest.mark.parametrize("job", JOBS)
def test_every_action_is_pinned_to_a_commit_sha(job: str) -> None:
    uses = _uses(job)
    assert uses, f"{job} uses no actions"
    unpinned = [u for u in uses if not SHA_PIN.match(u)]
    assert not unpinned, f"actions must be pinned to a full commit SHA: {unpinned}"


@pytest.mark.parametrize("job", JOBS)
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


@pytest.mark.parametrize("job", JOBS)
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


@pytest.mark.parametrize("job", JOBS)
def test_node_comes_from_nvmrc_with_the_npm_cache(job: str) -> None:
    with_ = _step(job, "actions/setup-node")["with"]
    assert with_["node-version-file"] == ".nvmrc"
    assert with_["cache"] == "npm"


@pytest.mark.parametrize("job", NX_JOBS)
def test_nx_cache_is_restored_and_keyed_on_lockfiles(job: str) -> None:
    with_ = _step(job, "actions/cache")["with"]
    assert with_["path"] == ".nx/cache"
    for lockfile in ("uv.lock", "package-lock.json"):
        assert lockfile in with_["key"]
    assert "github.sha" in with_["key"], "a fresh key per commit, so the cache is saved each run"
    restore_keys = with_["restore-keys"].strip().splitlines()
    assert len(restore_keys) == 1, "one restore key: the newest cache for the same lockfiles"
    assert with_["key"] == restore_keys[0] + "${{ github.sha }}", "restore key must be the key minus the commit"


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
    payload = re.search(r"<<'JSON'\n(.*?)\n\s*JSON\n", text, re.DOTALL)
    assert payload is not None, "ADR must carry the branch-protection payload"
    protection = json.loads(payload.group(1))
    checks = protection["required_status_checks"]
    assert sorted(checks["contexts"]) == sorted(_jobs())
    assert checks["strict"] is True
    assert protection["enforce_admins"] is True
