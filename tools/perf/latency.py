"""PERF-1 (PRD §12 Latency): measure the API's non-LLM endpoints and approval decision → run resumed.

Started by `tools/perf/run.sh`, which brings up the API and the orchestrator worker (stub executor: no
model is called) on the host against this worktree's compose stack. Everything is measured from the
client, through Caddy (`https://localhost:<port>/api`), with `time.perf_counter()` around a complete
request (response body read), one keep-alive HTTP/1.1 connection per client.

Phases:
1. **Sequential** (one client): every endpoint is warmed up, then measured `--samples` times; each round
   visits the endpoints in a fresh random order (seeded), so no endpoint always follows another.
2. **Concurrent** (`--concurrency` clients at once, the same rounds): reported, not part of the verdict.
3. **Decision → resumed**: `--decisions` runs of the seeded template are started; each pauses at its
   `manager_approval` task; the hire's manager decides through `POST /approvals/{id}/decision`; the clock
   starts just before that request and stops when `GET /runs/{id}` first shows a task after the approval
   started (`running` or `done`). The poll interval is part of the measured time (it only adds).

Every response must have the expected status code or the run aborts: a fast error must never count as
a fast success. Synthetic data only (`*.perf@example.test`, fixture numbers).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import random
import ssl
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats

ROOT = Path(__file__).resolve().parents[2]
SEED_CONFIG = ROOT / "config" / "seed.yaml"
TEMPLATE_KEY = "engineer-india"
APPROVAL_TASK = "manager_approval"
LIMIT_MS = 300.0
RESUME_LIMIT_S = 5.0
MIN_DECISIONS = 30
STARTED = frozenset({"running", "done"})
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

# Endpoints not measured, and why (written into the report).
EXCLUDED = {
    "POST /runs/{run_id}/chat": "LLM: the Run Assistant answers through a model (out of the non-LLM target)",
    "POST /runs/{run_id}/chat/confirm": "LLM-driven: confirms an action the Run Assistant proposed",
    "GET /runs/{run_id}/stream": "SSE stream held open for minutes; not a request/response latency",
    "POST /webhooks/telegram": "Telegram's callback, authenticated by the webhook secret; same decision "
    "path as the mock press (HIL-1 measured it in-process)",
    "POST /mock-outbox/{message_id}/press": "needs the notify worker's Telegram-mock messages; the web "
    "decision is the measured decision path",
    "POST /templates/drafts/{draft_id}/publish": "lifecycle action: each sample would publish a new template version",
    "POST /templates/{key}/versions/{version}/archive": "lifecycle action: would archive the seeded template",
    "GET /evals/runs/{run_id}": "no eval run exists in the seeded org (`GET /evals/runs` is measured, empty)",
    "POST /runs/{run_id}/cancel": "terminal: each sample would need a fresh run",
    "POST /runs/{run_id}/tasks/{task_id}/retry": "needs a failed task; the stub executor does not fail",
    "POST /runs/{run_id}/tasks/{task_id}/complete": "needs a failed task; the stub executor does not fail",
}


class MeasurementError(RuntimeError):
    """A request returned an unexpected status: the measurement is invalid and stops."""


@dataclass(frozen=True, slots=True)
class Users:
    hr_admin: tuple[str, str]
    hr: tuple[str, str]
    manager: tuple[str, str]
    manager_id: str


@dataclass(slots=True)
class Session:
    """One client: its HTTP connection and the bearer tokens of the seeded users."""

    http: httpx.Client
    tokens: dict[str, str] = field(default_factory=dict)

    def call(
        self, method: str, path: str, *, who: str | None, expect: int, **kwargs: Any
    ) -> tuple[float, httpx.Response]:
        headers = dict(kwargs.pop("headers", {}))
        if who is not None:
            headers["Authorization"] = f"Bearer {self.tokens[who]}"
        start = time.perf_counter()
        response = self.http.request(method, path, headers=headers, **kwargs)
        response.read()
        elapsed = time.perf_counter() - start
        if response.status_code != expect:
            raise MeasurementError(f"{method} {path}: {response.status_code}, expected {expect}")
        return elapsed, response


def _load_users() -> Users:
    config = yaml.safe_load(SEED_CONFIG.read_text())

    def login_of(role: str, user_id: str | None = None) -> tuple[str, str, str]:
        for user in config["users"]:
            if user["role"] == role and (user_id is None or user["id"] == user_id):
                password = os.environ.get(user["password_env"], "")
                if not password:
                    raise SystemExit(f"perf: {user['password_env']} is not set (load .env)")
                return user["email"], password, user["id"]
        raise SystemExit(f"perf: no {role} user in {SEED_CONFIG}")

    admin, hr, manager = login_of("HR_ADMIN"), login_of("HR"), login_of("MANAGER")
    return Users(admin[:2], hr[:2], manager[:2], manager_id=manager[2])


def _client(base_url: str, ca_file: Path) -> httpx.Client:
    if urlparse(base_url).hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("perf: only a local stack is measured (localhost)")
    # Caddy's dev certificate is issued by its own local CA (`run.sh` copies the root out of the container).
    return httpx.Client(base_url=base_url, verify=ssl.create_default_context(cafile=str(ca_file)), timeout=30.0)


def _login(session: Session, users: Users) -> None:
    for who, (email, password) in {"hr_admin": users.hr_admin, "hr": users.hr, "manager": users.manager}.items():
        _, response = session.call(
            "POST", "/auth/login", who=None, expect=200, json={"email": email, "password": password}
        )
        session.tokens[who] = response.json()["data"]["access_token"]


def _hire_body(manager_id: str) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:12]
    return {
        "name": f"Perf Hire {tag}",
        "role": "engineer",
        "location": "Bengaluru",
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "manager_id": manager_id,
        "pii": {"email": f"hire.{tag}.perf@example.test", "mobile": "+919000000000"},
    }


@dataclass(frozen=True, slots=True)
class Fixture:
    """The records the read endpoints read: one hire with a document, a run paused at its approval, a draft."""

    hire_id: str
    run_id: str
    template_version: int
    draft_id: str
    template: dict[str, Any]
    agent: str


def _template_version(session: Session) -> tuple[int, dict[str, Any]]:
    _, response = session.call("GET", "/templates", who="hr", expect=200)
    versions = [t for t in response.json()["data"] if t["key"] == TEMPLATE_KEY]
    if not versions:
        raise SystemExit(f"perf: template {TEMPLATE_KEY} is not published (make seed)")
    version = max(int(t.get("version") or t.get("latest_version")) for t in versions)
    _, body = session.call("GET", f"/templates/{TEMPLATE_KEY}/versions/{version}", who="hr", expect=200)
    return version, body.json()["data"]["template"]


def _new_hire(session: Session, manager_id: str) -> str:
    _, response = session.call("POST", "/hires", who="hr", expect=201, json=_hire_body(manager_id))
    return str(response.json()["data"]["id"])


def _new_run(session: Session, hire_id: str, version: int) -> str:
    body = {"hire_id": hire_id, "template_key": TEMPLATE_KEY, "template_version": version}
    _, response = session.call("POST", "/runs", who="hr", expect=201, json=body)
    return str(response.json()["data"]["id"])


def _fixture(session: Session, users: Users) -> Fixture:
    version, template = _template_version(session)
    hire_id = _new_hire(session, users.manager_id)
    session.call(
        "POST",
        f"/hires/{hire_id}/documents",
        who="hr",
        expect=201,
        params={"type": "pan"},
        content=TINY_PNG,
        headers={"Content-Type": "image/png"},
    )
    run_id = _new_run(session, hire_id, version)
    _, draft = session.call("POST", "/templates/drafts", who="hr", expect=201, json={"template": template})
    _, agents = session.call("GET", "/agents", who="hr_admin", expect=200)
    return Fixture(
        hire_id=hire_id,
        run_id=run_id,
        template_version=version,
        draft_id=str(draft.json()["data"]["id"]),
        template=template,
        agent=str(agents.json()["data"][0]["name"]),
    )


Probe = Callable[[Session], float]


def _get(path: str, who: str | None = "hr", **kwargs: Any) -> Probe:
    return lambda s: s.call("GET", path, who=who, expect=200, **kwargs)[0]


def _endpoints(fx: Fixture, users: Users) -> dict[str, Probe]:
    """Every non-LLM request/response endpoint, each as a probe returning one sample in seconds."""
    hire, run, version = fx.hire_id, fx.run_id, fx.template_version

    def put_draft(s: Session) -> float:
        # No `revision`: an unconditional save of the whole body (concurrent clients would 409 otherwise).
        body = {"template": fx.template}
        return s.call("PUT", f"/templates/drafts/{fx.draft_id}", who="hr", expect=200, json=body)[0]

    def pause_resume(s: Session, action: str) -> float:
        # Rounds keep pause before resume on the fixture run, so each is a legal move (running ↔ paused).
        return s.call("POST", f"/runs/{run}/{action}", who="hr", expect=200)[0]

    def login(s: Session) -> float:
        email, password = users.hr
        return s.call("POST", "/auth/login", who=None, expect=200, json={"email": email, "password": password})[0]

    return {
        "GET /health": _get("/health", who=None),
        "POST /auth/login": login,
        "GET /me": _get("/me"),
        "GET /agents": _get("/agents", who="hr_admin"),
        "GET /agents/{name}": _get(f"/agents/{fx.agent}", who="hr_admin"),
        "GET /audit": _get("/audit", who="hr_admin", params={"limit": 50}),
        "GET /evals/runs": _get("/evals/runs", who="hr_admin"),
        "GET /templates": _get("/templates"),
        "GET /templates/{key}/versions/{version}": _get(f"/templates/{TEMPLATE_KEY}/versions/{version}"),
        "GET /templates/drafts": _get("/templates/drafts"),
        "GET /templates/drafts/{draft_id}": _get(f"/templates/drafts/{fx.draft_id}"),
        "POST /templates/drafts": lambda s: s.call(
            "POST", "/templates/drafts", who="hr", expect=201, json={"template": fx.template}
        )[0],
        "PUT /templates/drafts/{draft_id}": put_draft,
        "POST /templates/drafts/{draft_id}/validate": lambda s: s.call(
            "POST", f"/templates/drafts/{fx.draft_id}/validate", who="hr", expect=200
        )[0],
        "GET /hires": _get("/hires"),
        "POST /hires": lambda s: s.call("POST", "/hires", who="hr", expect=201, json=_hire_body(users.manager_id))[0],
        "GET /hires/{hire_id}": _get(f"/hires/{hire}"),
        "GET /hires/{hire_id}/documents": _get(f"/hires/{hire}/documents"),
        "POST /hires/{hire_id}/documents": lambda s: s.call(
            "POST",
            f"/hires/{hire}/documents",
            who="hr",
            expect=201,
            params={"type": "pan"},
            content=TINY_PNG,
            headers={"Content-Type": "image/png"},
        )[0],
        "PUT /hires/{hire_id}/pii/{field}": lambda s: s.call(
            "PUT", f"/hires/{hire}/pii/mobile", who="hr", expect=200, json={"value": "+919000000001"}
        )[0],
        "POST /hires/{hire_id}/pii/{field}/reveal": lambda s: s.call(
            "POST",
            f"/hires/{hire}/pii/mobile/reveal",
            who="hr",
            expect=200,
            json={"reason": "Latency measurement (synthetic hire)"},
        )[0],
        "GET /pii-access-log": _get("/pii-access-log", who="hr_admin", params={"hire_id": hire}),
        "GET /runs": _get("/runs"),
        "POST /runs": lambda s: s.call(
            "POST",
            "/runs",
            who="hr",
            expect=201,
            json={"hire_id": hire, "template_key": TEMPLATE_KEY, "template_version": version},
        )[0],
        "GET /runs/{run_id}": _get(f"/runs/{run}"),
        "GET /runs/{run_id}/chat": _get(f"/runs/{run}/chat"),
        "POST /runs/{run_id}/pause": lambda s: pause_resume(s, "pause"),
        "POST /runs/{run_id}/resume": lambda s: pause_resume(s, "resume"),
        "GET /approvals": _get("/approvals", who="manager", params={"mine": "true"}),
        "GET /mock-outbox": _get("/mock-outbox", who="manager"),
    }


def _ordered_round(names: list[str], rng: random.Random) -> list[str]:
    """A shuffled round, keeping pause before resume (the only pair whose order matters)."""
    rest = [n for n in names if n not in {"POST /runs/{run_id}/pause", "POST /runs/{run_id}/resume"}]
    rng.shuffle(rest)
    if "POST /runs/{run_id}/pause" in names:
        at = rng.randrange(len(rest) + 1)
        rest[at:at] = ["POST /runs/{run_id}/pause", "POST /runs/{run_id}/resume"]
    return rest


def _measure(session: Session, probes: dict[str, Probe], rounds: int, rng: random.Random) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {name: [] for name in probes}
    for _ in range(rounds):
        for name in _ordered_round(list(probes), rng):
            samples[name].append(probes[name](session))
    return samples


def _sequential(base: str, users: Users, probes: dict[str, Probe], args: argparse.Namespace) -> dict[str, list[float]]:
    session = Session(_client(base, args.ca_file))
    _login(session, users)
    rng = random.Random(args.seed)  # noqa: S311 - visiting order, not security
    _measure(session, probes, args.warmup, rng)
    return _measure(session, probes, args.samples, rng)


def _concurrent(base: str, users: Users, args: argparse.Namespace) -> dict[str, list[float]]:
    # Each client works on its own hire, run and draft, as different HR users would: clients sharing one
    # record race on it (a pause against a pause is a legal 409; a reveal against a PII update hit a 500,
    # `concurrent-reveal-500.txt`), which is a correctness question, not latency.
    rounds = max(1, args.samples // args.concurrency)

    def worker(n: int) -> dict[str, list[float]]:
        session = Session(_client(base, args.ca_file))
        _login(session, users)
        probes = _endpoints(_fixture(session, users), users)
        rng = random.Random(args.seed + n + 1)  # noqa: S311 - visiting order, not security
        _measure(session, probes, 1, rng)
        return _measure(session, probes, rounds, rng)

    merged: dict[str, list[float]] = {}
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as pool:
        for result in pool.map(worker, range(args.concurrency)):
            for name, values in result.items():
                merged.setdefault(name, []).extend(values)
    return merged


def _wait(check: Callable[[], Any], timeout_s: float, poll_s: float, what: str) -> Any:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        found = check()
        if found:
            return found
        time.sleep(poll_s)
    raise MeasurementError(f"timed out after {timeout_s} s waiting for {what}")


def _resumed(run: dict[str, Any]) -> bool:
    return any(APPROVAL_TASK in t["depends_on"] and t["status"] in STARTED for t in run["tasks"])


def _one_decision(session: Session, users: Users, version: int, args: argparse.Namespace) -> tuple[float, float]:
    """Returns (decision request seconds, decision → resumed seconds) for one fresh run."""
    run_id = _new_run(session, _new_hire(session, users.manager_id), version)
    poll = args.poll_ms / 1000.0

    def run_state() -> dict[str, Any]:
        return dict(session.call("GET", f"/runs/{run_id}", who="hr", expect=200)[1].json()["data"])

    def paused() -> bool:
        return any(t["task_id"] == APPROVAL_TASK and t["status"] == "waiting_approval" for t in run_state()["tasks"])

    _wait(paused, args.resume_timeout, poll, f"run {run_id} to pause at its approval")

    def approval_id() -> str | None:
        _, response = session.call(
            "GET", "/approvals", who="manager", expect=200, params={"mine": "true", "status": "pending", "limit": 100}
        )
        ids = [a["id"] for a in response.json()["data"] if a.get("run_id") == run_id]
        return ids[0] if ids else None

    approval = _wait(approval_id, args.resume_timeout, poll, f"the approval of run {run_id}")
    t0 = time.perf_counter()
    request_s, _ = session.call(
        "POST", f"/approvals/{approval}/decision", who="manager", expect=200, json={"decision": "approve"}
    )
    _wait(lambda: _resumed(run_state()), args.resume_timeout, poll, f"run {run_id} to resume")
    return request_s, time.perf_counter() - t0


def _decisions(base: str, users: Users, version: int, args: argparse.Namespace) -> tuple[list[float], list[float]]:
    session = Session(_client(base, args.ca_file))
    _login(session, users)
    requests: list[float] = []
    resumed: list[float] = []
    for _ in range(args.decisions):
        request_s, resume_s = _one_decision(session, users, version, args)
        requests.append(request_s)
        resumed.append(resume_s)
    return requests, resumed


def _command(argv: list[str], timeout: float = 20) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv (sysctl, docker), never input
            argv, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _hardware() -> dict[str, Any]:
    memory = _command(["sysctl", "-n", "hw.memsize"])
    docker_memory = _command(["docker", "info", "--format", "{{.MemTotal}}"])
    names = (_command(["docker", "ps", "--format", "{{.Names}}"]) or "").split()
    project = os.environ.get("COMPOSE_PROJECT_NAME", "")
    return {
        "host": platform.platform(),
        "cpu": _command(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "ram_gib": round(int(memory) / 2**30, 1) if memory and memory.isdigit() else None,
        "docker_server": _command(["docker", "info", "--format", "{{.ServerVersion}}"]),
        "docker_cpus": _command(["docker", "info", "--format", "{{.NCPU}}"]),
        "docker_ram_gib": round(int(docker_memory) / 2**30, 1) if docker_memory and docker_memory.isdigit() else None,
        "containers_running": len(names),
        "containers_of_this_stack": sum(1 for n in names if project and n.startswith(f"{project}-")),
        "load_average_1_5_15": [round(v, 2) for v in os.getloadavg()],
        "python": platform.python_version(),
    }


def _table(results: dict[str, stats.Summary]) -> list[str]:
    lines = ["| Endpoint | n | p50 ms | p95 ms | p99 ms | max ms | p95 < 300 ms |", "|---|---|---|---|---|---|---|"]
    for name, s in sorted(results.items()):
        ok = "yes" if stats.within(s, limit_ms=LIMIT_MS) else "**no**"
        lines.append(f"| `{name}` | {s.count} | {s.p50_ms} | {s.p95_ms} | {s.p99_ms} | {s.max_ms} | {ok} |")
    return lines


def _report(report: dict[str, Any]) -> str:
    seq = {k: stats.Summary(**v) for k, v in report["sequential"].items()}
    conc = {k: stats.Summary(**v) for k, v in report["concurrent"].items()}
    resume = report["decision_to_resumed"]
    hw = report["hardware"]
    seq_verdict, conc_verdict = report["verdicts"]["sequential"], report["verdicts"]["concurrent"]
    lines = [
        "# PERF-1 · API latency and approval decision → run resumed",
        "",
        f"Measured {report['measured_at']} by `tools/perf/run.sh` (`tools/perf/latency.py`); raw samples in "
        "`latency.json`. PRD §12 targets: API p95 < 300 ms (non-LLM); approval decision → run resumed < 5 s "
        "(the card: at p95 over 30+ decisions).",
        "",
        "## Method",
        "",
        "- **Tool:** `tools/perf/latency.py`, a Python 3 client on `httpx` (one keep-alive HTTP/1.1 connection "
        "per client), timing each complete request (body read) with `time.perf_counter()`. Percentiles are "
        "nearest-rank (`tools/perf/stats.py`), never interpolated. Any unexpected status code aborts the run.",
        "- **Path measured:** client → Caddy (compose, TLS, `https://localhost:<CADDY_HTTPS_PORT>/api`) → the API "
        "(`uvicorn onb_api.main:app`, one process, on the host) → MongoDB 8 replica set (compose) through a "
        "loopback socat tunnel, traces exported over OTLP to the compose collector. The orchestrator worker "
        "(`python -m onb_orchestrator`, RUN-2's production worker, default 1 s queue poll) runs on the host "
        "with the default **stub executor**: no model was called.",
        f"- **Sample size:** {report['params']['warmup']} warm-up rounds discarded, then "
        f"{report['params']['samples']} samples per endpoint in the sequential phase (each round visits every "
        f"endpoint in a seeded random order, seed {report['params']['seed']}); concurrent phase: "
        f"{report['params']['concurrency']} clients at once, {report['params']['samples']} samples per endpoint "
        f"in all, each client on its own hire, run and draft; {resume['count']} approval decisions.",
        "- **Load:** the PRD names no load level. The verdict for box 1 is the sequential phase (one interactive "
        "client: the latency a user sees). The concurrent phase is reported beside it as information.",
        "- **Decision → resumed:** each decision is on a fresh run of the seeded template `engineer-india`, paused "
        "at `manager_approval`; the hire's manager decides with `POST /approvals/{id}/decision`; the clock "
        "starts before that request and stops when `GET /runs/{id}` first shows a task depending on the "
        f"approval as `running` or `done` (polled every {report['params']['poll_ms']} ms, which is inside the "
        "measured time).",
        "",
        "## Hardware",
        "",
        f"- Host: {hw['cpu']}, {hw['cpu_count']} CPUs, {hw['ram_gib']} GiB RAM, {hw['host']}, Python {hw['python']}.",
        f"- Docker Desktop {hw['docker_server']}: {hw['docker_cpus']} CPUs, {hw['docker_ram_gib']} GiB for Docker.",
        f"- A **shared dev Mac**: {hw['containers_running']} containers running during the measurement, of which "
        f"{hw['containers_of_this_stack']} are this stack (the rest belong to other, unrelated projects). Load "
        f"average (1/5/15 min) at the end: {hw['load_average_1_5_15']}. This was the only goal session running.",
        "- Not the VM: the deployment target (DEP-1) has not been measured.",
        "",
        "## Endpoints",
        "",
        f"Measured: {len(seq)} non-LLM endpoints (below). Not measured, and why:",
        "",
        *[f"- `{name}`: {why}" for name, why in report["excluded"].items()],
        "",
        f"## Result 1 · non-LLM endpoints, sequential (one client): {'PASS' if seq_verdict['passed'] else 'FAIL'}",
        "",
        *_table(seq),
        "",
        f"Failing: {', '.join(seq_verdict['failing']) or 'none'}.",
        "",
        f"## Result 2 · decision → run resumed, {resume['count']} decisions: {'PASS' if resume['passed'] else 'FAIL'}",
        "",
        "| Measure | n | p50 | p95 | p99 | max |",
        "|---|---|---|---|---|---|",
        f"| decision → resumed (s) | {resume['count']} | {resume['summary']['p50_ms'] / 1000:.3f} | "
        f"{resume['summary']['p95_ms'] / 1000:.3f} | {resume['summary']['p99_ms'] / 1000:.3f} | "
        f"{resume['summary']['max_ms'] / 1000:.3f} |",
        f"| `POST /approvals/{{id}}/decision` request (ms) | {resume['request_summary']['count']} | "
        f"{resume['request_summary']['p50_ms']} | {resume['request_summary']['p95_ms']} | "
        f"{resume['request_summary']['p99_ms']} | {resume['request_summary']['max_ms']} |",
        "",
        f"## Information · non-LLM endpoints, {report['params']['concurrency']} concurrent clients: "
        f"{'all under' if conc_verdict['passed'] else 'some over'} 300 ms at p95",
        "",
        *_table(conc),
        "",
        f"Over: {', '.join(conc_verdict['failing']) or 'none'}.",
        "",
    ]
    return "\n".join(lines)


def _summaries(samples: dict[str, list[float]]) -> dict[str, stats.Summary]:
    return {name: stats.summarise(values) for name, values in samples.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-file", type=Path, required=True, help="Caddy's local root CA certificate")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--decisions", type=int, default=40)
    parser.add_argument("--poll-ms", type=int, default=50)
    parser.add_argument("--resume-timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence" / "PERF-1")
    args = parser.parse_args(argv)
    if args.decisions < MIN_DECISIONS:
        parser.error(f"--decisions must be at least {MIN_DECISIONS} (the card's sample size)")

    users = _load_users()
    setup = Session(_client(args.base_url, args.ca_file))
    _login(setup, users)
    fx = _fixture(setup, users)
    probes = _endpoints(fx, users)

    print(f"perf: sequential, {len(probes)} endpoints x {args.samples}", flush=True)
    sequential = _summaries(_sequential(args.base_url, users, probes, args))
    print(f"perf: decisions x {args.decisions}", flush=True)
    requests, resumed = _decisions(args.base_url, users, fx.template_version, args)
    print(f"perf: concurrent, {args.concurrency} clients", flush=True)
    concurrent_ = _summaries(_concurrent(args.base_url, users, args))

    resume_summary = stats.summarise(resumed)
    seq_verdict = stats.verdict(sequential, limit_ms=LIMIT_MS, min_samples=args.samples)
    conc_verdict = stats.verdict(concurrent_, limit_ms=LIMIT_MS, min_samples=1)
    resume_passed = len(resumed) >= MIN_DECISIONS and resume_summary.p95_ms < RESUME_LIMIT_S * 1000
    report: dict[str, Any] = {
        "goal": "PERF-1",
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "params": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "hardware": _hardware(),
        "excluded": EXCLUDED,
        "sequential": {k: asdict(v) for k, v in sequential.items()},
        "concurrent": {k: asdict(v) for k, v in concurrent_.items()},
        "decision_to_resumed": {
            "count": len(resumed),
            "passed": resume_passed,
            "summary": asdict(resume_summary),
            "request_summary": asdict(stats.summarise(requests)),
            "samples_s": [round(v, 4) for v in resumed],
        },
        "verdicts": {"sequential": asdict(seq_verdict), "concurrent": asdict(conc_verdict)},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "latency.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "latency.md").write_text(_report(report))
    print(
        f"perf: box 1 (sequential p95 < 300 ms): {'PASS' if seq_verdict.passed else 'FAIL'}"
        f" failing={list(seq_verdict.failing)}"
    )
    print(
        f"perf: box 2 (decision -> resumed p95 < 5 s over {len(resumed)}): "
        f"{'PASS' if resume_passed else 'FAIL'} p95={resume_summary.p95_ms / 1000:.3f} s"
    )
    return 0 if seq_verdict.passed and resume_passed else 1


if __name__ == "__main__":
    sys.exit(main())
