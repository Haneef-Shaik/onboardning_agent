# Handoff — EVT-1 · Move work through a durable Mongo event bus        attempt 1 · goal/evt-1-event-bus · evidence captured at cf5d372 (see `git log -1` for the handoff commit)

**Outcome claimed.** `libs/py-eventbus` (`onb_eventbus`) publishes through an outbox: `publish()` runs inside
the tenant context and writes the domain change, an append-only `events` document and one job for each
subscribed consumer, all in one transaction. A repeated per-org idempotency key writes nothing. Workers claim
jobs by lease with an atomic `find_one_and_update`, renew the lease by heartbeat, and retry failures with
exponential backoff. A job is moved to `dead_letters` when its attempts are exhausted, when it is malformed,
or when its `org_id` is missing or invalid. Each consumer runs in its job's `TenantContext`, inside a
CONSUMER span continued from the producer's stored `traceparent`. To support this, `libs/py-data` gained
`DataStore.run_transaction` and the narrow cross-org `QueueRepository` (ADR 0002).

Gate commands run on the compose stack `ob-evt-1`, a Mongo 8 replica set. Commands use `.env`:
`set -a && . ./.env && set +a`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Two workers never process the same job at once (concurrency test) | yes | **Integration:** `libs/py-eventbus/tests_int/test_bus_against_mongo.py`, run by `make test-int` (`docs/evidence/EVT-1/make-test-int.txt`, `17 passed`, exit=0).<br>• `test_two_workers_never_process_the_same_job_at_once`: 60 jobs and 6 workers, each with its own client, thread and event loop, released together by a barrier. A cross-thread ledger asserts that no job is active in two handlers at once, that each job runs exactly once, that every job has `attempts == 1`, and that at least 2 workers shared the work.<br>• `test_one_job_raced_by_eight_workers_is_claimed_once`: 8 simultaneous claims on one job produce one winner.<br>**Unit:** `tests/test_worker.py::test_a_claimed_job_is_invisible_to_a_second_worker_until_its_lease_expires` and `::test_concurrent_workers_process_each_job_exactly_once_and_never_together`.<br>**Mutation M1** (a running job with a live lease made claimable) makes both integration tests fail. |
| A crashed worker's lease expires and the job is retried; exhausted jobs move to `dead_letters` (tests) | yes | **Integration** (same run):<br>• `test_a_crashed_workers_lease_expires_and_the_job_is_retried`: worker "crashed" is killed mid-handler. The job stays `running` with `lease_owner=crashed`. A second worker gets `None` while the lease holds. After `locked_until` passes it completes the job, with `attempt == 2` and `attempts == 2` in Mongo.<br>• `test_a_live_heartbeat_keeps_the_job_from_other_workers`: a rival makes 5 tries over 2.5 leases and gets nothing.<br>• `test_an_exhausted_job_moves_to_dead_letters`: outcomes are `[RETRIED, DEAD_LETTERED]`. `jobs` ends empty. The dead letter has `reason=exhausted`, `attempts=2`, `last_error="RuntimeError"` and no lease token.<br>• `test_a_job_whose_last_attempt_crashed_is_dead_lettered`: `reason=exhausted`, `last_error="lease expired"`.<br>**Unit:** retry backoff (2 s, then 4 s), the heartbeat extending the lease, and a lost lease that cancels the handler and writes nothing (`tests/test_worker.py`).<br>**Mutations:** M2 (no lease-expiry branch), M3 (`>=` changed to `>`), M4 (heartbeat stopped) and M5 (lease token not matched) are each caught. |
| A duplicate idempotency key is ignored; a rolled-back transaction leaves neither change nor event (tests) | yes | **Integration** (same run):<br>• `test_a_duplicate_idempotency_key_is_ignored`: one event and one job.<br>• `test_concurrent_publishes_with_one_key_write_one_event`: 10 parallel publishes give one event.<br>• `test_concurrent_publishes_with_one_key_apply_the_domain_change_once`: the upsert `$inc` counter is 1.<br>• `test_the_key_is_unique_per_org_not_globally`: the unique index `{org_id, idempotency_key}` enforced by the server.<br>• `test_a_rolled_back_transaction_leaves_neither_change_nor_event`: the change writes `onboarding_runs`, then raises. The root client then reads 0 runs, 0 events and 0 jobs.<br>• `test_a_server_side_write_error_rolls_back_the_change_and_the_event`: a duplicate `_id` refused by the server leaves no run, event or job.<br>**Unit:** `tests/test_publish.py` covers transient-conflict re-runs and a failure while the jobs are written.<br>**Mutations** M6 (existence check removed), M7 (session not passed) and M11 (publish outside a transaction) are each caught. |
| A consumer's span joins the producer's trace (test) | yes | **Integration:** `test_a_consumer_span_joins_the_producer_trace` (same run). The producer publishes inside a `POST /runs` span. The consumer runs on another thread and event loop and has no current span. The `consume orchestrator.start_run` span has the request's trace id, and its parent is the `publish run.started` span (PRODUCER). The handler sees the same trace id, and the job's stored `traceparent` holds it.<br>**Unit:** `tests/test_tracing.py::test_a_consumer_span_joins_the_producer_trace` also checks CONSUMER/PRODUCER kinds and the `org_id`/`run_id` attributes. It checks that the payload never appears in span attributes, and that a failed publish traces only the error type.<br>**Mutation M8** (the stored traceparent ignored) makes both fail. |
| A job without `org_id` is dead-lettered (test) | yes | **Integration:** `test_a_job_without_org_id_is_dead_lettered` (same run). A raw job without `org_id` is inserted. The worker returns `DEAD_LETTERED`, and the handler never runs. `jobs` is empty, and `dead_letters` holds `orphan` with `reason=missing_org_id` and no `org_id`.<br>**Unit:** `test_a_job_without_a_valid_org_id_is_dead_lettered_and_never_run[missing/empty/operator-chars/mapping]`.<br>**Mutation M9** (the org check skipped) is caught. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `bus = MongoEventBus(store, subscriptions={"run.started": ["orchestrator.start_run"]}, settings=QueueSettings(...))`, then `await bus.ensure_indexes()` once at startup. | `test_settings_and_interface.py`, integration tests |
| `with tenant_scope(org): await bus.publish(type, payload, idempotency_key=…, run_id=…, change=async_fn)` writes `change`, the event and one job per subscriber atomically. A duplicate key returns `PublishResult(duplicate=True, event_id=<existing>)` and writes nothing, `change` included. `change` can run more than once on a transient conflict, so it must only write through `store`. `publish` refuses to nest in another `run_transaction` (`TransactionError`). | box 3 tests, `test_a_transient_conflict_reruns_…`, `test_publish_does_not_nest_…` |
| Event types and consumer names are dotted lower-case words. The idempotency key is 1–200 characters, `run_id` 1–128. The payload must be a mapping holding ids and `ptk_` tokens only; the bus does not enforce this (see open findings). | `test_bad_publish_arguments_…`, `test_bad_subscriptions_…` |
| `worker = bus.worker({consumer: handler})`. Use `await worker.run(stop_event)` or `run_once()`, which returns a `Processed(job_id, outcome)` or `None`. Handlers receive a frozen `JobContext` with `job_id`, `org_id`, `consumer`, `event_id`, `event_type`, a read-only `payload`, `run_id` and `attempt`. The tenant context is already set. Delivery is **at least once**, so handlers must be idempotent. | `test_worker.py` |
| Leases default to 30 s with a 10 s heartbeat, at most `lease/2`. The default `max_attempts` is 5; backoff is 1 s doubling to a 300 s cap. A worker that loses its lease, or cannot renew it before it could lapse, cancels its handler and writes nothing. Stored errors and span statuses carry the exception **type** only. | `test_worker.py`, `QueueSettings` |
| `onb_data`: `await store.run_transaction(async_fn)` runs every repository call inside it in one transaction. A task created inside the callback cannot use the session (`TransactionError`). `store.queue("jobs"\|"dead_letters")` is for workers only (ADR 0002). | `libs/py-data/tests/test_transactions_and_queue.py`, `tests_int/test_transactions_and_queue.py` |
| `bus.consumers()` lists every subscribed consumer. This is the hook T-3 enumerates for cross-tenant cases. | `test_the_bus_lists_its_consumers_…` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-TENANT @ MS-6 | compose, 2026-11-10 | contributes: a consumer runs only in its job's org (it reads only that org, and a filter naming the other org is refused); a job without `org_id` is dead-lettered; the queue cannot be aimed at an org | `make-test-int.txt` (`test_a_consumer_sees_only_the_org_of_its_job`, `test_a_job_without_org_id_is_dead_lettered`), M10, M12–M14 | no (MS-6 is the last prover) |
| NF-DUR @ MS-4 | compose, 2026-11-10 | contributes: jobs at least once, with crashed leases retried and exhausted jobs dead-lettered; no run state yet (RUN-2) | `make-test-int.txt`, `mutation-checks.txt` | no (MS-4 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/EVT-1/make-check.txt`, cf5d372) |
| Integration | `make test-int` | exit=0. Stack checks `12 passed`; py-data `28 passed`; py-telemetry `3 passed`; py-eventbus `17 passed`; `Successfully ran target test-int for 3 projects` (`make-test-int.txt`) |
| Coverage | `nx run py-eventbus:test`, `nx run py-data:test` | py-eventbus `TOTAL 409 15 96%`, `77 passed`; py-data `TOTAL 441 2 99%`, `192 passed` (`unit-tests-coverage.txt`) |

`make test-xt` does not exist yet, because T-3 is not started. contracts-check, evals-selftest, test-pii and e2e do not exist yet either.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `connect`, `tenant_scope`, `TenantRepository` scoping, `ensure_index` org prefix, plain-dict copies | yes. They are extended, not changed, and all 144 T-1 unit tests still pass in the 192 |
| T-1 | Org id pattern `[A-Za-z0-9_-]{1,128}` | yes. The worker validates job org ids with `TenantContext` itself |
| F-2 | The compose Mongo is a replica set with transactions | yes. The rollback tests ran against it |
| F-4 | `inject_traceparent` / `continue_from` / `span_attributes`, and the `telemetry` fixture | yes. One trap is noted below |

## Mutation checks
`docs/evidence/EVT-1/mutation-checks.txt` (script `docs/evidence/EVT-1/mutate.py`): **18 of 18 caught**, every file restored
byte-for-byte, exit 0. Each mutation and some of the tests it failed:
- M1 a live-leased running job made claimable → int `test_two_workers_never_process_the_same_job_at_once`, `test_one_job_raced_by_eight_workers_is_claimed_once`; unit `test_concurrent_workers_process_each_job_exactly_once_and_never_together`
- M2 lease-expiry claim branch removed → `test_a_crashed_workers_lease_expires_and_the_job_is_retried` (unit + int), `test_a_job_whose_last_attempt_crashed_is_dead_lettered`
- M3 last-attempt check `>=` → `>` → `test_an_exhausted_job_moves_to_dead_letters` (unit + int)
- M4 heartbeat stopped → `test_a_heartbeat_keeps_extending_the_lease_while_the_handler_runs`, int `test_a_live_heartbeat_keeps_the_job_from_other_workers`
- M5 lease token not matched → `test_leased_updates_and_deletes_match_the_id_and_the_lease_token`, int `test_a_leased_write_with_a_stale_token_changes_nothing`
- M6 idempotency existence check removed → `test_a_duplicate_idempotency_key_is_ignored`, `test_a_duplicate_key_skips_the_domain_change_too`
- M7 session not passed to the driver → int `test_a_rolled_back_transaction_leaves_neither_change_nor_event`, `test_a_server_side_write_error_rolls_back_…`, unit `test_every_call_inside_a_transaction_carries_the_session`
- M8 stored traceparent ignored → `test_a_consumer_span_joins_the_producer_trace` (unit + int)
- M9 org check skipped → `test_a_job_without_a_valid_org_id_is_dead_lettered_and_never_run[*]`, int `test_a_job_without_org_id_is_dead_lettered`
- M10 tenant scope removed from the consumer → `test_a_consumer_reads_only_its_jobs_org`, int `test_a_consumer_sees_only_the_org_of_its_job`
- M11 publish outside a transaction → `test_a_rolled_back_transaction_leaves_neither_change_nor_event`, `test_a_failure_while_writing_the_jobs_rolls_back_…`
- M12–M15 the queue claim-filter allowlist, the worker-write allowlist, dead-letters-only insert/find, and the task-owned session → each caught by its named py-data test
- M16 `$type` guard removed → `test_a_job_with_a_corrupt_attempt_count_…`. M17 heartbeat failures ignored → `test_a_heartbeat_that_keeps_failing_…`. M18 publish span records exceptions → `test_a_failed_publish_traces_the_error_type_but_never_its_message`

## Review findings left open
Two reviews were run on `git diff main...HEAD` as general-purpose agents: one for code and one for security. Every high and medium finding was fixed in cf5d372:
- the queue allowlists and dead-letters-only writes (security, high)
- the publish span records only the error type (security, medium)
- a task-owned session (medium)
- jobs with a corrupt attempt count are skipped (medium)
- a handler's own `CancelledError` counts as a failure (medium)
- a heartbeat that keeps failing gives the job up (medium)

Also fixed: a stop during cleanup is no longer swallowed (low), the heartbeat must be at most `lease/2` (low), and `Scratch.uri` uses `repr=False` (low).

Left open:
- **low · `onb_eventbus/bus.py` publish · Tokens-only payloads are not enforced.** A producer that passes raw PII would store it in `events`, `jobs` and `dead_letters`. This belongs to PII-5/SEC-1 (a payload schema or scan) and is documented on `JobContext.payload`.
- **low · `worker.py` · Shutdown cancels the handler without awaiting it**, and each restart mid-job uses up an attempt. This is acceptable for at-least-once delivery. RUN-2 (restart survival) should decide on a graceful drain.
- **low · `documents.py` · Lease times use each worker's local clock.** Workers need NTP. `$$NOW` pipeline updates are an option if clocks drift.
- **low · `worker.py` exceptions escaping the consumer span** (database errors after the handler) are recorded by `continue_from` with their message. These are driver errors on job ids, not payloads. The span setting belongs to F-4's `continue_from` or to OBS-1 redaction.
- **low · jobs with a corrupt attempt count are skipped but never swept** into `dead_letters`. An operator sweep belongs with the ops/admin goals.
- **low · `tests_int` use the Mongo root login**, as T-1 and F-2 do. A least-privilege test user belongs to T-2.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session. I worked to the text printed by `plan.py goal EVT-1`.
- **"consumers registered in `make test-xt`"** (card scope, not a Done-when box): T-3 has not started, so `make test-xt` does not exist. The extension point is `EventBus.consumers()`. The consumer cross-tenant case exists in `tests_int/test_a_consumer_sees_only_the_org_of_its_job`, and its unit twin is `test_a_consumer_reads_only_its_jobs_org`. T-3 should lift it into the suite.
- There is no real consumer yet. RUN-1 adds the first one.

## Found for other goals
- **T-2 (command listener):** the claim is a `findAndModify` on `jobs` without `org_id`. Allow it, and the `{_id, lease_token}` writes on `jobs`/`dead_letters`, only for those commands (ADR 0002).
- **T-3:** enumerate `bus.consumers()` and add a two-org case per consumer.
- **RUN-1:** write run, `run_tasks` and `run.started` through `publish(..., change=…)`. Use a deterministic idempotency key such as `run:<run_id>:started`.
- **RUN-2:** graceful worker drain on shutdown. The resume path should use the job lease.
- **PII-5 / SEC-1:** a payload token-only check on `publish`, and a TTL or retention policy for `events`/`jobs`(done)/`dead_letters`.
- **F-4 / OBS-1:** `continue_from` records exception messages on the span (SDK default). Consider `record_exception=False`, or rely on redaction.
- **Ops:** a sweep that dead-letters jobs with non-numeric `attempts`, and a TTL index on done jobs.

## Decisions forced (ADRs written)
- **ADR 0002 — cross-org job queue** (`docs/adr/0002-cross-org-job-queue.md`):
  - `jobs` and `dead_letters` are tenant collections, with a narrow `QueueRepository` path for workers.
  - Non-unique queue indexes may lead with `status`; unique ones must still start with `org_id`.
  - `run_transaction` uses a contextvar session.
- **`jobs` are kept with `status: done`** after success rather than deleted. They are visible for debugging; a TTL is for ops.
- **Errors are stored and traced by type name only.** Messages can hold personal data.
- **A unit-test Mongo double** (`libs/py-eventbus/tests/memory_mongo.py`) gives semantic unit tests and coverage. Every box is also proved against the real replica set.

## Traps hit
- **OTel ProxyTracer caching.** A module-level `trace.get_tracer(...)` keeps the first real tracer it resolves, so spans vanished after the test fixture reset the provider. Fixed by fetching the tracer per call. Services that `init_telemetry` once are unaffected.
- **`with_transaction` re-runs the callback on transient errors.** `change` must be re-runnable. This is tested with a forced transient conflict.
- **`$inc` on a non-numeric field fails server-side.** One corrupt job would have stalled the queue, so the claim filter now requires numeric counters.
- **pytest collecting `libs/py-data/tests` and `libs/py-eventbus/tests` in one session errors out**, because both packages are named `tests`. Nx runs them separately, so the gates are unaffected.
- **The session shell refuses heredocs and complex compound commands.** Scripts live in the job temp directory.
