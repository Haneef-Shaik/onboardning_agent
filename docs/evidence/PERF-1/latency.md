# PERF-1 · API latency and approval decision → run resumed

Measured 2026-11-12 23:05 UTC (2026-11-13 04:35 IST local) by `tools/perf/run.sh` (`tools/perf/latency.py`); raw samples in `latency.json`. PRD §12 targets: API p95 < 300 ms (non-LLM); approval decision → run resumed < 5 s (the card: at p95 over 30+ decisions).

## Method

- **Tool:** `tools/perf/latency.py`, a Python 3 client on `httpx` (one keep-alive HTTP/1.1 connection per client), timing each complete request (body read) with `time.perf_counter()`. Percentiles are nearest-rank (`tools/perf/stats.py`), never interpolated. Any unexpected status code aborts the run.
- **Path measured:** client → Caddy (compose, TLS, `https://localhost:<CADDY_HTTPS_PORT>/api`) → the API (`uvicorn onb_api.main:app`, one process, on the host) → MongoDB 8 replica set (compose) through a loopback socat tunnel, traces exported over OTLP to the compose collector. The orchestrator worker (`python -m onb_orchestrator`, RUN-2's production worker, default 1 s queue poll) runs on the host with the default **stub executor**: no model was called.
- **Sample size:** 10 warm-up rounds discarded, then 200 samples per endpoint in the sequential phase (each round visits every endpoint in a seeded random order, seed 20261113); concurrent phase: 8 clients at once, 200 samples per endpoint in all, each client on its own hire, run and draft; 40 approval decisions.
- **Load:** the PRD names no load level. The verdict for box 1 is the sequential phase (one interactive client: the latency a user sees). The concurrent phase is reported beside it as information.
- **Decision → resumed:** each decision is on a fresh run of the seeded template `engineer-india`, paused at `manager_approval`; the hire's manager decides with `POST /approvals/{id}/decision`; the clock starts before that request and stops when `GET /runs/{id}` first shows a task depending on the approval as `running` or `done` (polled every 50 ms, which is inside the measured time).

## Hardware

- Host: Apple M5, 10 CPUs, 16.0 GiB RAM, macOS-27.2.1-arm64-arm-64bit, Python 3.12.13.
- Docker Desktop 29.8.2: 10 CPUs, 7.7 GiB for Docker.
- A **shared dev Mac**: 32 containers running during the measurement, of which 4 are this stack (the rest belong to other, unrelated projects). Load average (1/5/15 min) at the end: [4.37, 3.61, 3.79]. This was the only goal session running.
- Not the VM: the deployment target (DEP-1) has not been measured.

## Endpoints

Measured: 30 non-LLM endpoints (below), plus `POST /approvals/{approval_id}/decision`, measured in the decision phase (40 samples, Result 2) because each sample needs a fresh pending approval. Not measured, and why:

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
| `GET /agents` | 200 | 3.6 | 7.0 | 11.1 | 13.7 | yes |
| `GET /agents/{name}` | 200 | 3.1 | 6.0 | 9.8 | 24.3 | yes |
| `GET /approvals` | 200 | 51.5 | 60.0 | 63.3 | 76.8 | yes |
| `GET /audit` | 200 | 49.0 | 55.4 | 57.9 | 61.6 | yes |
| `GET /evals/runs` | 200 | 3.9 | 8.0 | 16.1 | 37.0 | yes |
| `GET /health` | 200 | 1.4 | 2.9 | 3.9 | 8.3 | yes |
| `GET /hires` | 200 | 48.9 | 54.6 | 58.8 | 85.5 | yes |
| `GET /hires/{hire_id}` | 200 | 2.8 | 6.3 | 9.6 | 16.0 | yes |
| `GET /hires/{hire_id}/documents` | 200 | 47.3 | 93.9 | 102.1 | 114.4 | yes |
| `GET /me` | 200 | 2.7 | 5.3 | 15.1 | 31.0 | yes |
| `GET /mock-outbox` | 200 | 4.0 | 7.8 | 13.3 | 26.1 | yes |
| `GET /pii-access-log` | 200 | 47.0 | 52.4 | 56.8 | 132.5 | yes |
| `GET /runs` | 200 | 52.2 | 61.4 | 84.0 | 108.0 | yes |
| `GET /runs/{run_id}` | 200 | 46.5 | 51.3 | 54.1 | 65.0 | yes |
| `GET /runs/{run_id}/chat` | 200 | 46.0 | 51.6 | 59.6 | 60.6 | yes |
| `GET /templates` | 200 | 2.9 | 5.3 | 8.0 | 10.1 | yes |
| `GET /templates/drafts` | 200 | 178.1 | 236.7 | 379.0 | 492.8 | yes |
| `GET /templates/drafts/{draft_id}` | 200 | 3.3 | 5.9 | 9.6 | 14.3 | yes |
| `GET /templates/{key}/versions/{version}` | 200 | 4.0 | 8.1 | 10.4 | 26.5 | yes |
| `POST /auth/login` | 200 | 183.1 | 202.5 | 237.8 | 291.7 | yes |
| `POST /hires` | 200 | 17.4 | 35.5 | 44.2 | 56.1 | yes |
| `POST /hires/{hire_id}/documents` | 200 | 10.6 | 25.9 | 36.7 | 69.6 | yes |
| `POST /hires/{hire_id}/pii/{field}/reveal` | 200 | 10.0 | 19.5 | 40.5 | 106.4 | yes |
| `POST /runs` | 200 | 14.6 | 25.2 | 37.2 | 40.8 | yes |
| `POST /runs/{run_id}/pause` | 200 | 142.8 | 158.3 | 166.1 | 177.9 | yes |
| `POST /runs/{run_id}/resume` | 200 | 147.6 | 162.4 | 185.0 | 240.8 | yes |
| `POST /templates/drafts` | 200 | 8.4 | 13.8 | 19.7 | 22.4 | yes |
| `POST /templates/drafts/{draft_id}/validate` | 200 | 5.4 | 9.9 | 11.7 | 17.7 | yes |
| `PUT /hires/{hire_id}/pii/{field}` | 200 | 17.6 | 36.2 | 48.1 | 59.0 | yes |
| `PUT /templates/drafts/{draft_id}` | 200 | 8.7 | 16.9 | 26.2 | 27.0 | yes |

Failing: none.

**Runs the orchestrator ended during pause/resume:** 0 (all phases). Each was found when its next pause was refused (409, not counted as a sample); that pause was then measured on a fresh run. A run that HR only paused and resumed should never end: see `pause-resume-run-failed.txt`.

## Result 2 · decision → run resumed, 40 decisions: PASS

| Measure | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| decision → resumed (s) | 40 | 1.005 | 1.055 | 1.255 | 1.255 |
| `POST /approvals/{id}/decision` request (ms) | 40 | 13.4 | 21.7 | 31.4 | 31.4 |

## Information · non-LLM endpoints, 8 concurrent clients: some over 300 ms at p95

| Endpoint | n | p50 ms | p95 ms | p99 ms | max ms | p95 < 300 ms |
|---|---|---|---|---|---|---|
| `GET /agents` | 200 | 7.1 | 125.8 | 139.0 | 155.4 | yes |
| `GET /agents/{name}` | 200 | 6.5 | 119.7 | 133.1 | 146.9 | yes |
| `GET /approvals` | 200 | 57.1 | 184.9 | 275.9 | 310.9 | yes |
| `GET /audit` | 200 | 53.1 | 174.9 | 265.7 | 282.6 | yes |
| `GET /evals/runs` | 200 | 6.7 | 123.1 | 246.5 | 256.0 | yes |
| `GET /health` | 200 | 2.1 | 12.0 | 123.9 | 134.1 | yes |
| `GET /hires` | 200 | 55.9 | 176.2 | 284.4 | 296.3 | yes |
| `GET /hires/{hire_id}` | 200 | 5.6 | 117.7 | 133.9 | 142.7 | yes |
| `GET /hires/{hire_id}/documents` | 200 | 7.7 | 131.6 | 137.2 | 160.7 | yes |
| `GET /me` | 200 | 5.2 | 101.3 | 127.4 | 265.1 | yes |
| `GET /mock-outbox` | 200 | 7.6 | 120.7 | 164.0 | 272.3 | yes |
| `GET /pii-access-log` | 200 | 9.5 | 156.3 | 230.7 | 280.1 | yes |
| `GET /runs` | 200 | 55.6 | 175.2 | 274.5 | 340.2 | yes |
| `GET /runs/{run_id}` | 200 | 7.5 | 126.7 | 147.2 | 270.6 | yes |
| `GET /runs/{run_id}/chat` | 200 | 8.2 | 123.1 | 178.6 | 253.2 | yes |
| `GET /templates` | 200 | 5.2 | 111.7 | 135.0 | 154.7 | yes |
| `GET /templates/drafts` | 200 | 213.9 | 413.9 | 474.9 | 515.0 | **no** |
| `GET /templates/drafts/{draft_id}` | 200 | 5.5 | 120.1 | 134.0 | 250.5 | yes |
| `GET /templates/{key}/versions/{version}` | 200 | 7.4 | 128.7 | 141.2 | 276.7 | yes |
| `POST /auth/login` | 200 | 205.7 | 322.3 | 386.4 | 449.4 | **no** |
| `POST /hires` | 200 | 39.4 | 289.6 | 322.6 | 430.3 | yes |
| `POST /hires/{hire_id}/documents` | 200 | 16.9 | 162.6 | 261.5 | 274.9 | yes |
| `POST /hires/{hire_id}/pii/{field}/reveal` | 200 | 19.9 | 153.9 | 249.5 | 377.9 | yes |
| `POST /runs` | 200 | 29.1 | 281.3 | 319.7 | 333.8 | yes |
| `POST /runs/{run_id}/pause` | 200 | 24.2 | 219.0 | 306.8 | 486.3 | yes |
| `POST /runs/{run_id}/resume` | 200 | 24.5 | 171.7 | 292.3 | 311.4 | yes |
| `POST /templates/drafts` | 200 | 11.3 | 137.3 | 251.6 | 281.1 | yes |
| `POST /templates/drafts/{draft_id}/validate` | 200 | 8.3 | 117.5 | 137.4 | 279.3 | yes |
| `PUT /hires/{hire_id}/pii/{field}` | 200 | 37.0 | 306.2 | 424.8 | 517.7 | **no** |
| `PUT /templates/drafts/{draft_id}` | 200 | 14.0 | 141.1 | 154.4 | 180.3 | yes |

Over: POST /auth/login, GET /templates/drafts, PUT /hires/{hire_id}/pii/{field}.

## Reading the numbers (written by hand after the run; everything above is generated)

- **Box 1 is met** with one client: all 30 measured non-LLM endpoints have p95 < 300 ms, and so does
  `POST /approvals/{id}/decision` (21.7 ms over 40). Slowest: `GET /templates/drafts` 236.7 ms,
  `POST /auth/login` 202.5 ms, `POST /runs/{id}/resume` 162.4 ms, `POST /runs/{id}/pause` 158.3 ms.
- **Box 2 is met:** 40 decisions, p95 1.055 s (max 1.255 s) against 5 s. Almost all of it is the worker's 1 s
  queue poll (RUN-2's default); the decision request itself is 21.7 ms at p95. HIL-1 measured the same path
  in-process (36 decisions, p95 1.087 s); this run goes through the HTTP route and Caddy.
- **Data volume at measurement time** (the org after five runs of this tool, read from Mongo just after):
  1,408 template drafts, 1,625 hires, 1,631 runs. That is far more than one org holds in the MVP, so these
  numbers are pessimistic for the paginated lists. **`GET /templates/drafts` has no pagination**
  (`templates/store.py` `list_drafts` returns every draft with its full body): its p50 grew from ~80 ms to
  178 ms between runs as the measurement added drafts, and it is the one endpoint whose sequential p95 is
  near the limit. It passes here, but its latency grows with the number of drafts (reported to the template goals).
- **Over 300 ms at p95 with 8 concurrent clients** (information, not the box-1 verdict): `POST /auth/login`
  322.3 ms (bcrypt at 12 rounds, PRD §7.8: ~180 ms of CPU per login in one API process, deliberate and
  once per session), `GET /templates/drafts` 413.9 ms (above), `PUT /hires/{id}/pii/{field}` 306.2 ms. The
  API runs as **one uvicorn process**; the other 27 endpoints stay under 300 ms at p95 with 8 clients. The
  PRD names no load level, and the box is judged with one client as stated in the method above.
- **A ~45 ms floor in the sequential phase on four reads** (`GET /runs/{id}` 46.5 ms p50, `/runs/{id}/chat`
  46.0, `/pii-access-log` 47.0, `/hires/{id}/documents` 47.3): the same requests have p50 7.5–9.5 ms with
  8 concurrent clients, so this is not server work. It looks like TCP delayed-ACK on one keep-alive
  connection over the Docker Desktop path. The list reads (`GET /approvals`, `/hires`, `/audit`, `/runs`)
  stay at 50–57 ms p50 under concurrency too, so that is their real cost. Everything is counted as measured,
  and the floor makes the sequential numbers worse, not better.
- **Shared dev Mac:** 32 containers running, 28 of them other, unrelated projects; load average 4.37 at
  the end. Docker Desktop has 7.7 GiB. Read the numbers as this code on this Mac. The VM (DEP-1) is not measured here.
- **Earlier attempts, kept as evidence, no samples reused:**
  - run 1 stopped on a **500** when 8 clients updated and revealed the same hire's mobile at once
    (`concurrent-reveal-500.txt`; the vault's `SupersededTokenError` reached the client as a 500). Since then
    each concurrent client has its own hire, run and draft.
  - run 3 stopped because the orchestrator **failed a run that was only paused and resumed** 8 ms apart
    (`pause-resume-run-failed.txt`, `TaskCancelled`, approval cancelled). The tool now records such a run as
    an incident (0 in this run) and pauses a fresh run.
  - run 4 hit the 25-minute timeout in the concurrent phase. Most likely one client failed before the shared start and left
    the others waiting; the API and worker logs showed no error, so the cause was not identified. Clients now
    release each other and raise the first real error. This run (5) completed.
