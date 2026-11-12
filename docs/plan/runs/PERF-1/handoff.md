# Handoff — PERF-1 · Measure API latency and approval-to-resume time        attempt 1 · goal/perf-1-latency-budgets · HEAD: see `git log -1`

**Outcome claimed.** The PRD §12 latency targets are measured on the compose stack, with the method written down.
`docs/evidence/PERF-1/latency.md` states the tool, hardware, sample size and endpoints. All 30 measured non-LLM
endpoints (and the decision route) have p95 < 300 ms with one client. Approval decision → run resumed has p95 1.055 s
over 40 decisions. No model was called (stub executor, `LLM_CONNECTOR=fake`).

Stack: compose project `ob-perf-1` (Caddy 19800/19801), seeded by `make seed`. The API (`uvicorn onb_api.main:app`,
one process) and the orchestrator worker (`python -m onb_orchestrator`, RUN-2's worker, stub executor) run on the
host behind Caddy, as UI-1 documents for dev serving. There is no compose service for either yet (DEP-1).

**Entry gate note.** RUN-2 is `done`. HIL-1 is `on hold` on the board: its code is merged to main (5f61a29), and only
its real-connector eval box is waiting on OW-1. The orchestrator directed this session to start PERF-1 on HIL-1's
merged routes (`POST /approvals/{id}/decision`).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `docs/evidence/PERF-1/latency.md` states the tool, hardware, sample size and endpoints, and non-LLM endpoints have p95 < 300 ms | yes | `docs/evidence/PERF-1/latency.md`. **Method** section: the tool is `tools/perf/latency.py` (httpx, `perf_counter` around full requests, nearest-rank percentiles, through Caddy with TLS verified against Caddy's local CA). **Hardware** section: Apple M5, 10 CPUs, 16 GiB; Docker Desktop 29.8.2 with 10 CPUs and 7.7 GiB; shared dev Mac with 32 containers, 28 of them other projects; load average 4.37. Sample size: 10 warm-up rounds, then 200 samples per endpoint. **Endpoints** section: 30 measured, plus the decision route; 11 excluded, each with a reason (2 LLM, SSE stream, Telegram webhook/press, lifecycle/terminal actions, one with no record to read). **Result 1: PASS**, `Failing: none.` The highest p95 is `GET /templates/drafts` at 236.7 ms. Run output `docs/evidence/PERF-1/run-output.txt`: `perf: box 1 (sequential p95 < 300 ms): PASS failing=[]`, `exit 0`. Raw samples: `latency.json` (`samples_s`). |
| Approval decision → run resumed has p95 < 5 s over 30+ decisions (same file) | yes | `latency.md` **Result 2: PASS**: 40 decisions, p50 1.005 s, **p95 1.055 s**, max 1.255 s. Each decision is on a fresh run of `engineer-india` paused at `manager_approval`; the manager decides via `POST /approvals/{id}/decision`. The clock stops at the first `GET /runs/{id}` (50 ms poll) showing a task after the approval `running`/`done`. `run-output.txt`: `perf: box 2 (decision -> resumed p95 < 5 s over 40): PASS p95=1.055 s`. The tool refuses `--decisions` < 30 (`test_fewer_than_thirty_decisions_is_refused`). |

Reproduce: `make up && make seed`, set `API_DEV_UPSTREAM=host.docker.internal:18800` in `.env` (recreate caddy), then
`set -a; . ./.env; set +a; tools/perf/run.sh` (defaults: 200 samples, 10 warm-up, 8 concurrent clients, 40 decisions).

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| On this Mac, one API process serves every non-LLM endpoint with p95 < 300 ms for one client. Even with 1.4k drafts / 1.6k hires / 1.6k runs in the org. | `latency.md` Result 1 |
| Decision → resumed is about 1 s, dominated by the worker's 1 s queue poll. Lowering the poll interval is the lever if it matters. | `latency.md` Result 2; HIL-1's in-process 1.087 s agrees |
| `tools/perf/run.sh` reruns the measurement against any worktree's compose stack (DEP-1 can adapt it for the VM). It verifies TLS against Caddy's CA and never calls a model. | `tools/perf/run.sh`, `tools/tests/test_perf_latency.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-LAT @ MS-6 | compose, 2026-11-13 (local; 2026-11-12 23:05 UTC) | pass | `docs/evidence/PERF-1/latency.md` | yes (last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | on 4d40f16 (the code at HEAD; later commits are docs only): `make check: all passed`, exit 0 (`docs/evidence/PERF-1/make-check.txt`): `plan ok: 67 goals, 34 ledger rows`, tooling tests `254 passed`, lint/typecheck/test for 17 projects, cross-tenant `362 passed`, mcp-hub test-xt ok |
| Tooling tests (inside check) | `uv run --frozen pytest tools/tests/test_perf_stats.py tools/tests/test_perf_latency.py -q` | `33 passed` |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |
| Integration / contracts / evals | — | not required: the diff touches only `tools/perf`, `tools/tests`, docs and ACCEPTANCE.md, with no `apps/*`, `libs/*` or `infra/` changes |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| HIL-1 | `POST /approvals/{id}/decision` decides; `GET /approvals?mine=true&status=pending` lists the approver's pending approvals | yes: 40/40 decisions returned 200 and resumed |
| HIL-1 | decision → resumed < 5 s p95 (in-process measurement) | yes: 1.055 s through HTTP (1.087 s in-process) |
| RUN-2 | the worker drives `run.started`/`run.resumed`; HR pause/resume are legal on running/paused runs | mostly. In run 3 the worker **failed a run that HR only paused and resumed** (see Found for other goals) |
| SEED-1 | `make seed` creates the org, users and published `engineer-india` | yes |
| UI-1 | dev serving: API on the host behind Caddy at `API_DEV_UPSTREAM` | yes |

## Mutation checks
`docs/evidence/PERF-1/mutation-checks.txt` (script `mutate.py`): **17 of 17 killed**, `source diff after all mutations: 0 files differ`.
- M1: `<=` instead of `<` → `test_an_endpoint_passes_only_when_its_p95_is_strictly_under_the_limit`
- M2: sample floor ignored → `test_the_verdict_fails_an_endpoint_measured_fewer_times_than_required`
- M3: empty passes → `test_the_verdict_of_nothing_measured_is_a_fail`
- M4: lower percentile rank → `test_the_percentile_is_nearest_rank`
- M5: status not checked → `test_an_unexpected_status_stops_the_measurement[*]`
- M6/M7: "resumed" too loose → `test_a_run_has_not_resumed_while_the_tasks_after_the_approval_only_wait`
- M8: pause/resume order → `test_every_round_visits_every_endpoint_once_with_pause_right_before_resume`
- M9/M11: decision and sample floors → `test_fewer_than_thirty_decisions_is_refused`, `test_fewer_than_a_hundred_samples_per_endpoint_is_refused`
- M10: non-local host → `test_only_a_local_stack_is_measured`
- M12: absolute paths → `test_report_paths_are_relative_to_the_repository`
- M13–M15: refused-pause handling → `test_a_pause_refused_on_a_live_run_stops_the_measurement[*]`, `test_a_pause_refused_because_the_run_ended_is_recorded_and_measured_on_a_fresh_run[*]`, `test_a_refused_resume_stops_the_measurement`
- M16/M17: concurrent client failure → `test_a_concurrent_client_that_fails_releases_the_others_and_its_own_error_is_raised`

## Review findings left open
One independent review (general-purpose agent, code and security) found no CRITICAL or HIGH issues. Fixed:
- MEDIUM: run.sh cleanup crashed on macOS bash 3.2 with an empty array, leaving the root-auth tunnels up. Tunnels are now removed first.
- MEDIUM: the health check could pass against a stale API process. Fix: port-free check at start, `kill -0` on both started processes, and leftover listeners are stopped on exit.
- MEDIUM: the md's "45 ms floor" bullet named the wrong endpoints. It now names only the four the data supports.
- LOW: per-endpoint raw samples were not saved; they are now in `latency.json`.
- LOW: the decision route was not listed with the endpoints; it now is.
- LOW: the sample floor was circular; there is now a fixed `MIN_SAMPLES = 100`.
- LOW: absolute home paths appeared in evidence; paths are now relative.
- LOW: concurrent clients did not start together; a barrier now holds them.
- LOW: the time zones were unlabelled; both UTC and local are now stated.

Left open:
- **LOW · `tools/perf/run.sh`: the API and worker connect as the Mongo root user**, as `scripts/seed.sh` does. The compose stack defines no app user. The URI exists only in child-process environments; `reports/perf/*.log` are git-ignored. This should change once compose or DEP-1 has an app user.
- **LOW · concurrent phase is information only.** The PRD names no load level, so box 1 is judged with one client (stated up front in the method). With 8 clients, `POST /auth/login` (bcrypt, deliberate), `GET /templates/drafts` (unpaginated) and `PUT /hires/{id}/pii/{field}` exceed 300 ms at p95.

## Left undone, and why
- The VM is not measured (DEP-1 does not exist yet). The numbers are for a shared dev Mac.
- The Telegram decision path (`POST /mock-outbox/{id}/press`) is not measured over HTTP: it needs the notify worker. HIL-1 measured it in-process at p95 1.102 s.
- `docs/plan/board.html` was not rebuilt or republished. ACCEPTANCE.md changed; the orchestrator can run `plan.py build --board`.
- I could not set `/goal` from this orchestrated session. I worked to the text `plan.py goal PERF-1` printed.

## Found for other goals
- **RUN-2 / HIL-1 (orchestrator), likely MEDIUM: a run that HR pauses and resumes quickly can be failed by the worker.**
  - Evidence: `pause-resume-run-failed.txt`. The run was parked at `manager_approval`. Pause and resume came 8 ms apart, then again about 500 ms later. About 20 ms after that, the worker logged `run … failed: TaskCancelled`: the run went `running → failed`, the pending approval was cancelled, and every remaining task was cancelled.
  - It happened once in roughly a thousand pause/resume pairs across the five runs; run 5 had none in about 410 pairs.
  - A double-click on Pause then Resume could lose a run. This looks related to RUN-2's open LOW, "two drivers can briefly share a run after a quick pause/resume".
- **PII goals (PII-1/PII-3 owners), MEDIUM: a reveal concurrent with an update of the same field returns 500.**
  - Evidence: `concurrent-reveal-500.txt`. The vault's `SupersededTokenError`, raised when the token read before a `PUT /hires/{id}/pii/{field}` is superseded, reaches the client as a 500.
  - It should be a 409, or a retry with the new token.
- **TPL-2 / templates, LOW: `GET /templates/drafts` is unpaginated** (`templates/store.py` `list_drafts` returns every draft with its full body). It reached 236.7 ms p95 at 1,408 drafts and grows linearly. Add `page`/`limit` like `/hires` and `/runs`.
- **Observation:** about 45 ms per request on some reads over a single keep-alive connection through Docker Desktop. This is consistent with TCP delayed-ACK and was not investigated; it only makes the sequential numbers worse.

## Decisions forced (ADRs written)
None. ADR 0029 (reserved) is not used.

## Traps hit
- The API exports OTLP over HTTP, so the collector tunnel must target 4318; 4317 is gRPC. With 4317, spans failed to export and the traced cost was missing.
- Chat routes are 503 without `AGENT_TOKEN_KEY` + `MCP_HUB_URL`. run.sh sets a key and a closed loopback hub URL, so the history read is measured and nothing can reach the assistant.
- Role matrix: `/agents`, `/audit`, `/evals/runs` and `/pii-access-log` are HR_ADMIN only. `/pii/{field}/reveal` needs a `reason`.
- Measurement data accumulates in the org across runs (drafts, hires, runs). The final run was not done on a fresh database; `latency.md` states the volume.
