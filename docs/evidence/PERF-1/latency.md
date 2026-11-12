# PERF-1 · API latency and approval decision → run resumed

Measured 2026-11-12 22:16 UTC by `tools/perf/run.sh` (`tools/perf/latency.py`); raw samples in `latency.json`. PRD §12 targets: API p95 < 300 ms (non-LLM); approval decision → run resumed < 5 s (the card: at p95 over 30+ decisions).

## Method

- **Tool:** `tools/perf/latency.py`, a Python 3 client on `httpx` (one keep-alive HTTP/1.1 connection per client), timing each complete request (body read) with `time.perf_counter()`. Percentiles are nearest-rank (`tools/perf/stats.py`), never interpolated. Any unexpected status code aborts the run.
- **Path measured:** client → Caddy (compose, TLS, `https://localhost:<CADDY_HTTPS_PORT>/api`) → the API (`uvicorn onb_api.main:app`, one process, on the host) → MongoDB 8 replica set (compose) through a loopback socat tunnel, traces exported over OTLP to the compose collector. The orchestrator worker (`python -m onb_orchestrator`, RUN-2's production worker, default 1 s queue poll) runs on the host with the default **stub executor**: no model was called.
- **Sample size:** 10 warm-up rounds discarded, then 200 samples per endpoint in the sequential phase (each round visits every endpoint in a seeded random order, seed 20261113); concurrent phase: 8 clients at once, 200 samples per endpoint in all, each client on its own hire, run and draft; 40 approval decisions.
- **Load:** the PRD names no load level. The verdict for box 1 is the sequential phase (one interactive client: the latency a user sees). The concurrent phase is reported beside it as information.
- **Decision → resumed:** each decision is on a fresh run of the seeded template `engineer-india`, paused at `manager_approval`; the hire's manager decides with `POST /approvals/{id}/decision`; the clock starts before that request and stops when `GET /runs/{id}` first shows a task depending on the approval as `running` or `done` (polled every 50 ms, which is inside the measured time).

## Hardware

- Host: Apple M5, 10 CPUs, 16.0 GiB RAM, macOS-27.2.1-arm64-arm-64bit, Python 3.12.13.
- Docker Desktop 29.8.2: 10 CPUs, 7.7 GiB for Docker.
- A **shared dev Mac**: 32 containers running during the measurement, of which 4 are this stack (the rest belong to other, unrelated projects). Load average (1/5/15 min) at the end: [8.97, 5.76, 5.17]. This was the only goal session running.
- Not the VM: the deployment target (DEP-1) has not been measured.

## Endpoints

Measured: 30 non-LLM endpoints (below). Not measured, and why:

- `POST /runs/{run_id}/chat`: LLM: the Run Assistant answers through a model (out of the non-LLM target)
- `POST /runs/{run_id}/chat/confirm`: LLM-driven: confirms an action the Run Assistant proposed
- `GET /runs/{run_id}/stream`: SSE stream held open for minutes; not a request/response latency
- `POST /webhooks/telegram`: Telegram's callback, authenticated by the webhook secret; same decision path as the mock press (HIL-1 measured it in-process)
- `POST /mock-outbox/{message_id}/press`: needs the notify worker's Telegram-mock messages; the web decision is the measured decision path
- `POST /templates/drafts/{draft_id}/publish`: lifecycle action: each sample would publish a new template version
- `POST /templates/{key}/versions/{version}/archive`: lifecycle action: would archive the seeded template
- `GET /evals/runs/{run_id}`: no eval run exists in the seeded org (`GET /evals/runs` is measured, empty)
- `POST /runs/{run_id}/cancel`: terminal: each sample would need a fresh run
- `POST /runs/{run_id}/tasks/{task_id}/retry`: needs a failed task; the stub executor does not fail
- `POST /runs/{run_id}/tasks/{task_id}/complete`: needs a failed task; the stub executor does not fail

## Result 1 · non-LLM endpoints, sequential (one client): PASS

| Endpoint | n | p50 ms | p95 ms | p99 ms | max ms | p95 < 300 ms |
|---|---|---|---|---|---|---|
| `GET /agents` | 200 | 3.5 | 9.8 | 21.5 | 22.6 | yes |
| `GET /agents/{name}` | 200 | 2.9 | 6.9 | 17.8 | 40.2 | yes |
| `GET /approvals` | 200 | 48.8 | 56.5 | 69.3 | 180.5 | yes |
| `GET /audit` | 200 | 47.3 | 54.4 | 70.6 | 94.5 | yes |
| `GET /evals/runs` | 200 | 3.8 | 8.0 | 14.6 | 110.5 | yes |
| `GET /health` | 200 | 1.4 | 3.1 | 4.4 | 5.9 | yes |
| `GET /hires` | 200 | 48.2 | 58.5 | 76.7 | 291.6 | yes |
| `GET /hires/{hire_id}` | 200 | 2.7 | 6.1 | 10.7 | 12.5 | yes |
| `GET /hires/{hire_id}/documents` | 200 | 46.7 | 95.0 | 107.4 | 123.8 | yes |
| `GET /me` | 200 | 2.5 | 6.7 | 11.1 | 83.3 | yes |
| `GET /mock-outbox` | 200 | 3.9 | 8.8 | 25.6 | 48.3 | yes |
| `GET /pii-access-log` | 200 | 46.3 | 52.4 | 64.4 | 73.1 | yes |
| `GET /runs` | 200 | 49.8 | 62.3 | 86.0 | 176.8 | yes |
| `GET /runs/{run_id}` | 200 | 45.9 | 53.2 | 73.4 | 110.9 | yes |
| `GET /runs/{run_id}/chat` | 200 | 45.6 | 52.2 | 71.0 | 109.0 | yes |
| `GET /templates` | 200 | 2.7 | 8.5 | 13.9 | 22.6 | yes |
| `GET /templates/drafts` | 200 | 80.5 | 149.1 | 202.0 | 230.8 | yes |
| `GET /templates/drafts/{draft_id}` | 200 | 3.0 | 6.6 | 13.8 | 36.0 | yes |
| `GET /templates/{key}/versions/{version}` | 200 | 3.7 | 8.9 | 13.4 | 41.2 | yes |
| `POST /auth/login` | 200 | 180.3 | 231.9 | 357.9 | 1090.3 | yes |
| `POST /hires` | 200 | 15.9 | 36.4 | 80.1 | 112.0 | yes |
| `POST /hires/{hire_id}/documents` | 200 | 10.1 | 22.9 | 110.5 | 122.5 | yes |
| `POST /hires/{hire_id}/pii/{field}/reveal` | 200 | 9.4 | 22.3 | 30.8 | 53.3 | yes |
| `POST /runs` | 200 | 12.9 | 38.2 | 59.2 | 91.5 | yes |
| `POST /runs/{run_id}/pause` | 200 | 139.8 | 156.2 | 167.8 | 228.0 | yes |
| `POST /runs/{run_id}/resume` | 200 | 141.7 | 165.6 | 187.4 | 207.8 | yes |
| `POST /templates/drafts` | 200 | 6.9 | 14.0 | 21.0 | 37.0 | yes |
| `POST /templates/drafts/{draft_id}/validate` | 200 | 4.9 | 13.2 | 44.8 | 99.6 | yes |
| `PUT /hires/{hire_id}/pii/{field}` | 200 | 16.7 | 33.6 | 56.5 | 96.5 | yes |
| `PUT /templates/drafts/{draft_id}` | 200 | 7.9 | 17.9 | 38.0 | 88.1 | yes |

Failing: none.

## Result 2 · decision → run resumed, 40 decisions: PASS

| Measure | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| decision → resumed (s) | 40 | 0.997 | 1.052 | 1.067 | 1.067 |
| `POST /approvals/{id}/decision` request (ms) | 40 | 13.1 | 18.8 | 19.1 | 19.1 |

## Information · non-LLM endpoints, 8 concurrent clients: some over 300 ms at p95

| Endpoint | n | p50 ms | p95 ms | p99 ms | max ms | p95 < 300 ms |
|---|---|---|---|---|---|---|
| `GET /agents` | 200 | 7.5 | 58.2 | 129.9 | 192.9 | yes |
| `GET /agents/{name}` | 200 | 8.0 | 66.1 | 97.3 | 116.0 | yes |
| `GET /approvals` | 200 | 59.4 | 148.8 | 190.4 | 436.9 | yes |
| `GET /audit` | 200 | 53.0 | 127.8 | 177.1 | 197.5 | yes |
| `GET /evals/runs` | 200 | 9.3 | 86.6 | 158.6 | 215.7 | yes |
| `GET /health` | 200 | 2.2 | 8.1 | 65.0 | 104.4 | yes |
| `GET /hires` | 200 | 59.0 | 137.1 | 192.1 | 217.7 | yes |
| `GET /hires/{hire_id}` | 200 | 5.9 | 38.1 | 110.5 | 162.1 | yes |
| `GET /hires/{hire_id}/documents` | 200 | 9.0 | 71.0 | 133.6 | 209.2 | yes |
| `GET /me` | 200 | 6.4 | 75.0 | 113.6 | 127.2 | yes |
| `GET /mock-outbox` | 200 | 9.5 | 58.0 | 126.2 | 134.2 | yes |
| `GET /pii-access-log` | 200 | 10.1 | 116.6 | 165.1 | 384.3 | yes |
| `GET /runs` | 200 | 57.6 | 156.6 | 174.6 | 253.5 | yes |
| `GET /runs/{run_id}` | 200 | 9.6 | 93.2 | 200.6 | 229.0 | yes |
| `GET /runs/{run_id}/chat` | 200 | 8.6 | 70.6 | 117.3 | 206.7 | yes |
| `GET /templates` | 200 | 5.9 | 61.5 | 96.2 | 131.0 | yes |
| `GET /templates/drafts` | 200 | 127.5 | 241.4 | 278.5 | 423.5 | yes |
| `GET /templates/drafts/{draft_id}` | 200 | 6.1 | 51.8 | 120.8 | 163.2 | yes |
| `GET /templates/{key}/versions/{version}` | 200 | 8.4 | 74.5 | 122.0 | 159.6 | yes |
| `POST /auth/login` | 200 | 210.7 | 339.6 | 402.0 | 453.7 | **no** |
| `POST /hires` | 200 | 46.6 | 190.8 | 481.9 | 524.1 | yes |
| `POST /hires/{hire_id}/documents` | 200 | 23.4 | 120.8 | 177.2 | 300.6 | yes |
| `POST /hires/{hire_id}/pii/{field}/reveal` | 200 | 21.5 | 141.1 | 261.2 | 366.9 | yes |
| `POST /runs` | 200 | 35.6 | 187.8 | 269.5 | 558.5 | yes |
| `POST /runs/{run_id}/pause` | 200 | 32.4 | 167.9 | 339.3 | 468.4 | yes |
| `POST /runs/{run_id}/resume` | 200 | 32.2 | 155.6 | 325.6 | 657.5 | yes |
| `POST /templates/drafts` | 200 | 12.2 | 79.9 | 113.1 | 216.1 | yes |
| `POST /templates/drafts/{draft_id}/validate` | 200 | 9.8 | 65.0 | 122.8 | 157.9 | yes |
| `PUT /hires/{hire_id}/pii/{field}` | 200 | 44.0 | 199.1 | 407.1 | 427.7 | yes |
| `PUT /templates/drafts/{draft_id}` | 200 | 15.2 | 100.2 | 170.1 | 307.4 | yes |

Over: POST /auth/login.

## Reading the numbers (written by hand after the run; everything above is generated)

- **Box 1 is met** on the stated method: every one of the 30 measured non-LLM endpoints has p95 < 300 ms with
  one client (slowest: `POST /auth/login` 231.9 ms, then `POST /runs/{id}/resume` 165.6 ms,
  `GET /templates/drafts` 149.1 ms).
- **Box 2 is met:** 40 decisions, p95 1.052 s (max 1.067 s) against 5 s. Almost all of it is the worker's 1 s
  queue poll (RUN-2's default); the decision request itself is 18.8 ms at p95. HIL-1 measured the same path
  in-process (36 decisions, p95 1.087 s); this run goes through the HTTP route and Caddy.
- **Login is the one endpoint over 300 ms under 8 concurrent clients** (p95 339.6 ms). That is bcrypt at
  12 rounds (PRD §7.8, ~180 ms of CPU per login, serialised in one API process); it is a once-per-session
  call and its cost is deliberate. It is not part of the box-1 verdict (one client), and it is said here so
  nobody reads the PASS as "login is under 300 ms at any load". On this hardware, with one uvicorn process,
  the other 29 endpoints stay under 300 ms at p95 with 8 concurrent clients.
- **A ~45 ms floor on some reads in the sequential phase** (`GET /runs/{id}`, `/runs/{id}/chat`,
  `/pii-access-log`, `/approvals`, `/hires`, `/audit`): the same requests have p50 6–10 ms under concurrency,
  so this is not server work. It looks like TCP delayed-ACK interacting with a single keep-alive connection
  on the Docker Desktop loopback path. It is counted as measured (it makes the sequential numbers worse, not
  better) and was not investigated further.
- **Shared, noisy machine:** load average 8.97 at the end, 28 containers of other projects running. The
  numbers are an upper bound for this code on this Mac, not a VM figure. The VM (DEP-1) is not measured here.
- **The first full run stopped on a 500** in the concurrent phase when 8 clients updated and revealed the
  same hire's mobile at once (`concurrent-reveal-500.txt`: the vault refused a superseded token and the API
  returned 500). The concurrent phase now gives each client its own records; the race is reported to the PII
  goals, not hidden: no sample of that run was kept.
