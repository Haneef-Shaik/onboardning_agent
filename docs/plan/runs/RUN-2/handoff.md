# Handoff — RUN-2 · Survive restarts and let HR control a run        attempt 2 · goal/run-2-run-control · rebased on main df2ad35 (RUN-3 merged) · see `git log -1`

**Outcome claimed.** A worker killed with SIGKILL mid-run restarts and the run completes with no task effect applied twice; a task failing past its attempts blocks the run and queues the HR notification in the same transaction; HR pauses, resumes, cancels, retries and manually completes (reason mandatory for completion, everything audited); managers see only their reports' runs; `GET /runs/{id}/stream` emits every task transition. Design: `docs/adr/0018-run-recovery-and-control.md`.

Gates ran on compose project `ob-run-2` (Mongo 8 replica set). Evidence is in `docs/evidence/RUN-2/`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A worker killed with SIGKILL mid-run restarts and the run completes; a counter proves no task's side effect ran twice (test) | yes | **Integration, real process + real Mongo:** `apps/orchestrator/tests_int/test_kill_and_restart.py::test_a_worker_killed_with_sigkill_mid_run_restarts_and_the_run_completes_with_no_effect_twice[before]` and `[after]` (`kill-and-restart.txt`: `12 passed` for `tests_int`). A real `python kill_worker.py` is killed with SIGKILL while a task is `running` (before its effect / after its effect), a second worker is started, the run is `completed`; the sink counter (`applied.log`, keyed by `idempotency_key`) has no key twice; tasks done before the kill were called once, the killed one twice with the same key, `attempts == 2`, a `takeover` history note; `[after]` shows the retry's effect deduplicated (`deduplicated.log`); then SIGTERM makes the second worker exit 0 (drain). 24 consecutive kills passed after the fix below (before it, ~3 of 8 runs ended `failed: IncompleteRun`). **Unit:** `apps/orchestrator/tests/test_run_control.py::test_a_task_whose_holder_died_is_taken_over_once_its_lease_ran_out_and_runs_with_the_same_key`, `::test_a_task_another_worker_still_holds_is_not_taken_over_or_run`, `::test_a_worker_that_lost_its_lease_stops_its_task_and_does_not_finish_it`, `::test_a_task_that_keeps_crashing_workers_stops_at_its_max_attempts_and_blocks_the_run`, `libs/py-agents/tests/test_run_control_store.py::test_a_task_whose_lease_ran_out_is_taken_over_once`, `apps/orchestrator/tests/test_checkpointer.py::test_a_task_whose_writes_were_cut_short_by_a_crash_is_not_half_applied`. Mutations M1, M2, M3, M17, M18, M20 kill. |
| A task failing past its max attempts blocks the run and records an HR notification (test) | yes | **Unit:** `test_run_control.py::test_a_failing_task_is_retried_with_backoff_and_then_succeeds` (backoff 2 s, 4 s), `::test_a_task_failing_past_its_max_attempts_blocks_the_run_and_records_an_hr_notification` (run `blocked`, task `failed` with error type, one `run.blocked {run_id, task_id}` event), `::test_hr_retrying_the_failed_task_...`, `::test_hr_completing_the_failed_task_by_hand_...`. **Integration:** `tests_int/test_block_and_resume_against_mongo.py::test_a_blocked_run_queues_the_hr_notification_with_the_block_and_resumes_through_the_bus`: on the real outbox the event `run.blocked` has exactly one job for consumer `notify.in_app` in the same org, and the resumed run completes. Mutations M4, M5 kill. |
| Manual complete without a reason → 422; with one → audited (tests) | yes | `apps/api/tests/test_run_control_routes.py::test_completing_a_task_by_hand_without_a_reason_is_422_and_changes_nothing[*]` (5 bodies: missing, empty, blank, null, non-string), `::test_completing_a_task_by_hand_with_a_reason_is_audited_and_resumes_the_run` (audit `run.task.complete`, target `run:<id>/task:<tid>`, the reason, the actor; the reason is not stored on the task), `libs/py-contracts/tests/test_run_control_contracts.py::test_completing_a_task_by_hand_needs_a_reason`. Pause, resume, cancel, retry are audited too (`::test_hr_pauses_resumes_and_cancels_a_run_and_each_is_audited_and_announced`). Mutations M6, M7 kill. |
| Managers list only their reports' runs (test) | yes | `test_run_control_routes.py::test_hr_lists_every_run_of_the_org_newest_first_and_a_manager_only_their_reports` (Ravi gets his two, Anita her one), `::test_a_manager_without_reports_lists_nothing_and_another_orgs_runs_never_appear`, `::test_a_run_is_read_with_its_tasks_by_hr_and_by_the_hires_manager_only` (other manager and other org: 404, identical to a missing run); cross-tenant `tools/tests_xt/test_xt_api.py::test_the_routes_own_cross_tenant_checks[GET /runs]` and `[GET /runs/{run_id}]`. Mutations M8, M9 kill. |
| The SSE stream emits each task transition (test) | yes | `test_run_control_routes.py::test_the_stream_emits_each_task_transition_in_order_and_ends_with_the_run` (each task's exact `pending→ready→running→done`, the run's moves, then `end`), `::test_the_stream_follows_a_run_live_until_it_finishes` (transitions made from another thread while the stream is open; ends with `end {status: completed}`), `::test_the_stream_is_scoped_like_the_run`, `::test_a_stream_ends_after_its_lifetime_so_the_client_reconnects`. Mutation M10 kills. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Effect idempotency contract.** Every attempt of a task carries `TaskWork.idempotency_key = "<run_id>:<task_id>"`. A task killed after its effect and before `done` runs again; **RUN-3 and the MCP hub must make every effectful tool apply its effect at most once per key.** | `tests_int/test_kill_and_restart.py[after]` |
| **States.** Run: `started, running, paused, blocked, completed, failed, cancelled` (`RunState.active` = started/running/paused/blocked). Task adds `failed → ready` (retry) and `failed → done` (manual). Contracts and the TS client carry them. | `test_run_states.py`, `test_run_contracts.py`, `make contracts-check` |
| **Run bus subscriptions** (`onb_agents.runs.SUBSCRIPTIONS`): `run.started→orchestrator.start_run`, `run.resumed→orchestrator.resume_run`, `run.cancelled→orchestrator.cleanup_run`, `run.blocked→notify.in_app`; `run.paused` and the retired `run.task_changed` have no consumer. `control_key(run_id, kind, n)` is the idempotency key of a control event. | `test_run_store.py::test_the_run_bus_*`, xt coverage (4 consumers) |
| **Records.** Tasks now carry `lease_token`, `lease_until`, `error` (type only), `retry_at`, `history` ([{from,to,at,by,note?}]); runs carry `history`. `RunStore.move_task(..., by=, fields=)`, `take_over`, `renew_lease`, `release_lease`, `list_runs`, `move_run(..., by=, expected_status=)`. A running task is moved only by its lease holder. | `test_run_control_store.py` |
| **Handler.** `start_run_handler(store, executor, clock, *, bus=None, policy=None, drain=None, sleep=None)`; a run that is not `started`/`running` is left alone; `RunPolicy(max_attempts=3, backoff_base=2, backoff_max=60, lease_seconds=30)` from `ORCHESTRATOR_*` env. Failed past attempts → `blocked`, not `failed`; `failed` is for deterministic errors. Completed/failed/cancelled runs delete their checkpoints. | `test_run_control.py`, `test_worker_main.py` |
| **Graceful drain.** `onb_eventbus.ReleaseJob` hands a job back with no attempt used; SIGTERM drains (task in hand finishes, job released, exit 0). | `libs/py-eventbus/tests/test_worker.py::test_a_released_job_*`, kill test |
| **API.** `GET /runs` (`status`, `page`, `limit`), `GET /runs/{id}`, `POST /runs/{id}/{pause,resume,cancel}`, `POST /runs/{id}/tasks/{task_id}/{retry,complete}`, `GET /runs/{id}/stream` (SSE `task`/`run`/`end`); reads for any role (manager scoped), controls HR/HR_ADMIN; `RunService.control`, `stream_poll_seconds`, `stream_max_seconds`. | `test_run_control_routes.py`, role matrix, xt |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-DUR @ MS-4 | compose, 2026-11-12 | pass: no run state lost on worker restart (SIGKILL, both stages); jobs at least once with idempotent effects | `docs/evidence/RUN-2/kill-and-restart.txt`, `make-test-int.txt` | yes (last prover) |
| U4 @ MS-5 | compose, 2026-11-12 | contributes: HR pauses, resumes, cancels, retries, completes; runs stream live | `test_run_control_routes.py`, `make-check.txt` | no (MS-5 is the last prover) |

## Gates
All re-run on the rebased branch (attempt 2), compose project `ob-run-2`.

| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed` (`docs/evidence/RUN-2/make-check.txt`): lint, typecheck and test pass for 17 projects; orchestrator `154 passed`, cross-tenant `316 passed`, `cross-tenant surface: 36 API routes, 9 MCP tools, 5 event consumers`, mcp-hub xt `13 passed`. |
| Integration | `make test-int` | `NX Successfully ran target test-int for 10 projects` (`make-test-int.txt`); orchestrator `16 passed` (RUN-1's 8, RUN-3's 4, kill ×2, block/resume, plus the changed budget test). |
| Kill test | `apps/orchestrator/tests_int/test_kill_and_restart.py` | `2 passed` ×6 consecutive runs after the rebase |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Eval self-test | `make evals-selftest` | `result: PASS` (`make-evals-selftest.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-1 | `run_bus` is the only bus builder; tenant checkpointer; compare-and-set moves | yes, extended (subscriptions, `adelete_thread`, token CAS). Where FR-R4 supersedes RUN-1, tests changed: a failing task now blocks the run (`test_run_graph`, `test_run_failures`), finished runs delete checkpoints (`test_runs_against_mongo` ×2, xt consumer test), `RunStatus` has more members. |
| NOTIF-1 | `run.blocked {run_id, task_id}` and `notify.in_app` | yes: the run bus now subscribes it; the real outbox queues the job (integration test). |
| AUD-1 | an action whose audit write fails is not done | yes: state change, audit and event commit in one `bus.publish(change=...)`. |
| EVT-1 | at-least-once, leases, retries | yes; added `ReleaseJob`. |
| T-3 | route/consumer coverage | yes: 8 route cases and 2 consumer cases added. |

## Mutation checks
`docs/evidence/RUN-2/mutation-checks.txt` (script `mutate.py`): **24 of 24 killed** (M21–M24 added in attempt 2), `source diff after all mutations: 0 files differ`. Examples: M1 takeover disabled → `test_a_task_whose_holder_died_is_taken_over...`; M2 lease-token CAS removed → `test_a_worker_that_was_taken_over_cannot_finish_the_task...`; M3 half-written checkpoint writes applied → `test_a_task_whose_writes_were_cut_short...`; M4 one attempt too many → `test_a_task_failing_past_its_max_attempts...`; M5 no `run.blocked` → same test; M6 reason optional → contract test; M7 no audit → audit test; M8/M9 manager scope → box 4 tests; M10 stream skips transitions → box 5; M11 `resume` on a never-paused run → conflict test; M12 pause ignored; M13 drain ignored; M14 release costs an attempt (eventbus); M15 no checkpoint delete; M16/M17 attempt accounting; M18 endless takeover; M19 manager may control (role matrix); M20 lease never renewed. M21 stale `failure` not cleared → `test_a_sink_rejection_counts_as_an_attempt_and_the_retry_starts_clean`; M22 deterministic failure retried → `test_an_executor_error_that_retrying_cannot_fix_blocks_the_run_at_once`; M23 database error fails the task → `test_a_database_error_from_the_executor_is_not_the_tasks_failure`; M24 sink-failed task not an attempt → same test as M21. Not covered by a mutation here: the SIGKILL integration test itself (it failed ~3/8 before the checkpoint fix, 0/24 after).

## Review findings left open
Code and security reviews (two independent general-purpose agents) found no CRITICAL. Fixed with tests: HIGH a pause overridden by the start's `started→running` (now `expected_status`), HIGH a run left `running` after its tasks were cancelled (now failed), MEDIUM concurrent retries leaving a run blocked, MEDIUM a pause during the last task failing the job, MEDIUM heartbeat dying on a non-database error. Left open:
- **MEDIUM · `consumer.py` · a dead-lettered job leaves its run `running` with no driver** (the job's attempts used by crashes and transient errors). Nothing reacts to a dead letter yet → ops/alerting goal (OBS/PERF); recovery is `POST .../pause` then `resume`, which publishes a fresh job.
- ~~any non-transient executor error fails the run~~ **fixed in attempt 2**: executor errors are task attempts (retry, then block); an executor can say `retryable = False`; database errors retry the job.
- **MEDIUM · `routes.py`/`stream.py` · the stream checks access once, runs up to an hour, polls twice a second per stream, no per-user cap.** Add a re-check every N polls and a concurrent-stream limit → SEC-2 (rate limiting).
- **MEDIUM · `runner.py` · lease expiry uses each worker's wall clock** (skew over a third of the lease can cause a double execution; the idempotency key is the guard) → deploy/ops: NTP or Mongo server time.
- **MEDIUM (product) · `POST /runs` is still not idempotent over HTTP.** Not built; the two options are in ADR 0018 §9 for Haneef/SEC-2.
- **LOW** the audit `reason` is free text (GR-1/SEC-1); `HireStore.ids` unbounded; a client retry of a succeeded control returns 409; a takeover keeps the old `error`; two drivers can briefly share a run after a quick pause/resume (task CAS prevents double execution); the run-id path pattern is `^[A-Za-z0-9_-]{1,64}$` (the eval seed's ids do not match `run_<24hex>`).

## Attempt 2: rebase onto RUN-3 (main df2ad35)
The orchestrator authorised the rebase. To resolve the conflicts once instead of through 12 commits, the branch was squashed to one commit on its old base (fcc6ab9) and rebased; a second commit reconciles semantics (so the 12 attempt-1 commits are no longer separate). Conflicts and how they were resolved, semantically:
- **`events.py`/`__init__.py`/`store.py`:** both sides kept. RUN-3's `FAIL_TASK`, `SINK_REJECTED`, `record_model`, `record_outcome(only_if=)`, `run_tokens` stay; `RUN_TASK_CHANGED` (unused in attempt 1) was dropped. `fail_running` now also releases the lease, appends to history and sets `error`, and applies only to the lease holder it read.
- **`runner.py`/`consumer.py`/`worker.py`:** RUN-3's task span, `orchestrator.run` span, `fail_task_handler`, agent-executor selection (`ORCHESTRATOR_EXECUTOR`) and `ConfigError` (now from `agents_setup`) kept with my leases, retry/block, controls, drain and policy. The worker runs four consumers: `start_run`, `resume_run`, `cleanup_run`, `fail_task`. The executor future is now created *inside* the task span (RUN-3's span-tree test caught it nested under the run).
- **Exact-list tests extended, not loosened:** `test_run_store` now asserts the exact `SUBSCRIPTIONS` map (5 events) and the exact sorted consumer tuple; `test_agents_setup`, `test_worker_main` and the xt consumer table name all four orchestrator consumers (`tools/tests_xt/test_xt_consumers.py` has cases for each).

Decisions recorded in ADR 0018 addendum (§10–15):
- **A sink-rejected `fail_task` is an ordinary failed attempt:** the task is retried while attempts remain, and at the maximum the run is **blocked** (HR notified), not failed. The cause stays on the task (`failure`, `error = sink_rejected`).
- **A retry starts clean:** `error`, `retry_at` and `failure` are cleared when a task re-enters `running` (RUN-3's open item).
- **Recovery of a run whose driver died after `fail_task`:** the redelivered job finds the failed task and retries/blocks it (test `test_a_run_whose_driver_died_after_a_sink_failed_its_task_is_recovered_by_the_next_delivery`). A dead-lettered job is still the open item.
- **Executor error classification (RUN-3's agent executor):** `AgentTaskFailedError.retryable` is false for `no_agent`, `retired_agent`, `grant_refused`, `budget_exceeded`, `guardrail_blocked`: the task fails once and the run blocks at once; other errors (`timeout`, `invalid_output`, `error`, rejections) are attempts; database errors release the lease, hand the attempt back and retry the job.
- **RUN-3 tests changed where FR-R4 supersedes them** (not loosened): agent failures now end the run `blocked` with the remaining tasks `pending` (was `failed`/`cancelled`) in `test_agent_executor` (4 asserts), `test_sink_rejected` (2), and the integration budget test; RUN-3's harness uses a no-wait 3-attempt policy and a bus stand-in.

## Left undone, and why
- No compose service for the orchestrator worker (RUN-1 left it; still the deployment goal's). The kill test runs the real worker module as a process.
- `docs/plan/board.html` was not regenerated or republished (I edited ACCEPTANCE.md; the orchestrator can run `plan.py build --board` and republish).
- I could not set the `/goal` condition from this orchestrated session; I worked to the text from `plan.py goal RUN-2`.

## Found for other goals
- **RUN-3:** executors/tools must dedupe on `TaskWork.idempotency_key`; classify transient agent errors; `waiting_approval` still goes through the stub path (HIL).
- **UI-5:** the stream sends `task`, `run` and `end` events; reconnect on `end {reason: "timeout"}`; EventSource cannot send the bearer header, use fetch streaming.
- **PII-5/SEC-2:** the audit reason of the controls is free text.
- **test fixtures:** the eval seed keeps `onboarding_runs` of another shape in the same collection; the run routes ignore documents without `dag_hash`.

## Decisions forced (ADRs written)
- ADR 0018 (this goal): leases and takeover, effect idempotency, retry/block, controls, history-based stream, drain, checkpoint retention (incl. whole-task writes), HTTP idempotency left open.

## Traps hit
- A task's LangGraph writes are several documents: a kill between two left a task half recorded and the run ended with tasks never started. Found by the SIGKILL test; fixed with a per-task write count.
- `started → running` is a legal engine move, so `resume` worked on a never-paused run; resume now requires `paused`.
- My generated `.env` filled non-secret variables (LLM model names, fake script) and broke `py-evals` tests; only secrets should be generated.
