"""PERF-1: the guards of `tools/perf/latency.py` that keep the measurement honest. No stack: mocked HTTP."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("perf_latency", ROOT / "tools" / "perf" / "latency.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["perf_latency"] = module
    spec.loader.exec_module(module)
    return module


latency = _load()


def _session(status: int) -> Any:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"data": {}}))
    session = latency.Session(httpx.Client(base_url="https://localhost/api", transport=transport))
    session.tokens["hr"] = "token"
    return session


def test_a_response_with_the_expected_status_is_timed() -> None:
    elapsed, response = _session(200).call("GET", "/me", who="hr", expect=200)
    assert response.status_code == 200
    assert elapsed >= 0.0


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 500, 503])
def test_an_unexpected_status_stops_the_measurement(status: int) -> None:
    # A fast error must never be counted as a fast success.
    with pytest.raises(latency.MeasurementError, match=str(status)):
        _session(status).call("GET", "/me", who="hr", expect=200)


def _run(*tasks: tuple[str, list[str], str]) -> dict[str, Any]:
    return {"tasks": [{"task_id": t, "depends_on": deps, "status": s} for t, deps, s in tasks]}


def test_a_run_has_resumed_once_a_task_after_the_approval_has_started() -> None:
    approval = latency.APPROVAL_TASK
    assert latency._resumed(_run((approval, [], "done"), ("laptop", [approval], "running")))
    assert latency._resumed(_run((approval, [], "done"), ("laptop", [approval], "done")))


def test_a_run_has_not_resumed_while_the_tasks_after_the_approval_only_wait() -> None:
    approval = latency.APPROVAL_TASK
    assert not latency._resumed(_run((approval, [], "waiting_approval"), ("laptop", [approval], "pending")))
    assert not latency._resumed(_run((approval, [], "done"), ("laptop", [approval], "ready")))
    # A task that does not depend on the approval says nothing about the resume.
    assert not latency._resumed(_run((approval, [], "done"), ("orientation", [], "running")))


def test_every_round_visits_every_endpoint_once_with_pause_right_before_resume() -> None:
    names = ["GET /me", "GET /runs", "POST /runs/{run_id}/pause", "POST /runs/{run_id}/resume", "POST /hires"]
    rng = random.Random(1)  # noqa: S311 - visiting order, not security
    orders = set()
    for _ in range(50):
        order = latency._ordered_round(names, rng)
        assert sorted(order) == sorted(names)
        at = order.index("POST /runs/{run_id}/pause")
        assert order[at + 1] == "POST /runs/{run_id}/resume"
        orders.add(tuple(order))
    assert len(orders) > 1  # the order changes between rounds


def test_fewer_than_thirty_decisions_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        latency.main(["--base-url", "https://localhost/api", "--ca-file", "x.crt", "--decisions", "29"])
    assert stopped.value.code == 2  # argparse's usage error, before anything is contacted
    assert "--decisions must be at least 30" in capsys.readouterr().err


def test_fewer_than_a_hundred_samples_per_endpoint_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    # The verdict's sample floor is fixed, not whatever was asked for: `--samples 5` cannot make a PASS.
    with pytest.raises(SystemExit) as stopped:
        latency.main(["--base-url", "https://localhost/api", "--ca-file", "x.crt", "--samples", "99"])
    assert stopped.value.code == 2
    assert "--samples must be at least 100" in capsys.readouterr().err


def test_report_paths_are_relative_to_the_repository() -> None:
    assert latency._relative(latency.ROOT / "docs" / "evidence" / "PERF-1") == "docs/evidence/PERF-1"
    assert latency._relative(Path("/somewhere/else/root.crt")) == "root.crt"


def _pause_probe(run_status: str, refused: str = "pause") -> tuple[Any, list[dict[str, str]], list[str]]:
    """The pause/resume probes against a fake API whose first `refused` call on run_old is a 409."""
    calls: list[str] = []
    seen = {"refused": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path.endswith(f"/runs/run_old/{refused}") and not seen["refused"]:
            seen["refused"] = True
            return httpx.Response(409, json={"data": None})
        if request.method == "GET" and path.endswith("/runs/run_old"):
            return httpx.Response(200, json={"data": {"status": run_status}})
        if request.method == "POST" and path.endswith("/runs"):
            return httpx.Response(201, json={"data": {"id": "run_new"}})
        return httpx.Response(200, json={"data": {}})

    session = latency.Session(httpx.Client(base_url="https://localhost/api", transport=httpx.MockTransport(handler)))
    session.tokens.update(hr="t", hr_admin="t", manager="t")
    fixture = latency.Fixture("h_1", "run_old", 1, "d_1", {}, "db_agent")
    users = latency.Users(("a", "p"), ("b", "p"), ("c", "p"), manager_id="u_m")
    incidents: list[dict[str, str]] = []
    probes = latency._endpoints(fixture, users, incidents)
    return (lambda action: probes[f"POST /runs/{{run_id}}/{action}"](session)), incidents, calls


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
def test_a_pause_refused_because_the_run_ended_is_recorded_and_measured_on_a_fresh_run(status: str) -> None:
    probe, incidents, calls = _pause_probe(status)
    assert probe("pause") >= 0.0
    assert [i["run_id"] for i in incidents] == ["run_old"]
    assert incidents[0]["status"] == status
    assert calls[-2:] == ["POST /api/runs", "POST /api/runs/run_new/pause"]
    probe("resume")
    assert calls[-1] == "POST /api/runs/run_new/resume"  # later rounds use the fresh run


@pytest.mark.parametrize("status", ["running", "started", "paused", "blocked"])
def test_a_pause_refused_on_a_live_run_stops_the_measurement(status: str) -> None:
    probe, incidents, _ = _pause_probe(status)
    with pytest.raises(latency.MeasurementError, match="409"):
        probe("pause")
    assert incidents == []


def test_a_refused_resume_stops_the_measurement() -> None:
    probe, incidents, _ = _pause_probe("failed", refused="resume")
    with pytest.raises(latency.MeasurementError, match="409"):
        probe("resume")
    assert incidents == []


def test_only_a_local_stack_is_measured(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="local"):
        latency._client("https://example.com/api", tmp_path / "root.crt")
