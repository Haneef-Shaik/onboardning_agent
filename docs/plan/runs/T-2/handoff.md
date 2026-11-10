# Handoff — T-2 · Catch any unscoped query at runtime        attempt 4 · goal/t-2-query-safety-net · see `git log -1` (evidence captured at 83a8052 + evidence commits)

**Attempt 4 (orchestrator request, after goal-verifier attempt 3 passed on base 707b60c).**
- Rebased onto main `1e22bba` (MCP-3, PII-4, AUTH-1 and C-1 work). 16 commits, no conflicts.
- After the rebase, `tools/tests/test_safety_net_suites.py` flagged two new Mongo suites, `apps/api/tests_int` and `apps/mcp-mongo/tests_int`, as the check is designed to. Both now load the plugin and have a fixture test.
- PII-4's py-crypto Mongo tests (`test_grants_mongo.py`, `test_vault_mongo.py`) now run under the guard.
- **Every flag in those suites was test code, none was product code.** A diagnostic plugin (kept outside the repo) recorded the innermost repo frame of each flagged command. All 103 flags (api 1, mcp-mongo 90, py-crypto 12; `docs/evidence/T-2/attempt4-flag-call-sites.txt`) traced to `tests_int/` lines, and no `src/` frame appeared in any stack. That includes the API's login and directory lookup, the MCP server in its uvicorn thread, and the vault and grant resolvers.
- The flagged sites were:
  - root read-backs and counts across orgs;
  - the mcp-mongo `seeded` cleanup;
  - one deliberate vault tamper (re-pointing a token so the vault must fail closed);
  - raw-driver refusal checks, which prove MongoDB itself refuses denied stages for the MCP users.

  Each is now in a named `unscoped("reason")` block. Each affected assertion compares the same query result, now held in a variable filled inside the block, so no assertion changed.
- No product code calls `allowing_unscoped` (grep of `apps/*/src`, `libs/*/src`).
- Mutants P4 and P5 cover the two new suites.

**Attempt 3 (goal-verifier attempt 2 failed box 2).** `libs/py-crypto/tests_int` (PII-1) is a Mongo
suite that `make test-int` runs, and it was not under the net. Its two root `find({})` reads on
`org_keys` went unflagged. Changes in this attempt:
- The three hooks are now one shared plugin, `onb_data.safety_net_pytest`, that each suite's conftest imports.
- All four `test-int` suites that touch Mongo load it: py-data, py-eventbus, py-crypto, and py-telemetry, which opens a pymongo client for its OTel span test.
- py-crypto's two root reads are in named `unscoped(...)` blocks with identical assertions.
- Each suite has a fixture test.
- `tools/tests/test_safety_net_suites.py` makes `make check` fail for any present or future `test-int` suite that imports pymongo or `onb_data` without the plugin and a fixture test.
- The plugin itself is tested by running throwaway pytest sessions in a subprocess. Mutants P1 to P3 cover the new wiring.

**Outcome claimed.** `onb_data.safety_net` adds a PyMongo command listener (`TenantCommandListener`).
It flags every command on a tenant collection that is not pinned to exactly one `org_id`.
`install_safety_net()` registers it globally. Every Mongo integration suite (`libs/py-data/tests_int`
and `libs/py-eventbus/tests_int`) installs it before any client exists and wraps each test in
`listener.guard()`, so a test that sends an unscoped command fails. The event bus worker's
deliberate cross-org commands (ADR 0002) are allowed by exact collection and command shape, and
nothing else on `jobs`/`dead_letters` is. `make test-int` also checks every index on every
database on the server and fails on a tenant index that does not start with `org_id`. The one
exception is the ADR 0002 rule that non-unique indexes on the queue collections may lead with
queue state.

**Attempt 2 changes.**
- Rebased onto main `707b60c` (PII-3 fix and EVT-1). One conflict, in `errors.py`; both
  `TransactionError` and `UnscopedCommandError` are kept.
- Added the worker-queue allowance (`onb_data/queue_allowance.py`) and the queue index rule.
- Put the eventbus integration suite under the guard and marked its root read-backs.
- Added 10 more mutation checks. Both gates now pass.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A raw unscoped `find` through a deliberately leaked client trips the listener (test) | yes | `libs/py-data/tests_int/test_safety_net.py::test_an_unscoped_find_through_a_leaked_client_trips_the_listener` takes `store._client` from `connect()` (the deliberate leak) and runs `find({})` on `hires`. It asserts that rows from both orgs came back, then that `tenant_safety_net.check()` raises `UnscopedCommandError` matching `unscoped find on <db>.hires`. `test_an_unscoped_find_through_a_raw_client_trips_the_listener` does the same with a raw `MongoClient`. Its value sentinel is now `Bina-sentinel-xyz`, which can't occur in a hex database name; the old `b1` collided once with a random name. Run with `make test-int`: py-data `49 passed`, exit 0 (`docs/evidence/T-2/make-test-int.txt`). Mutants M1, M2 and M4 fail it (`docs/evidence/T-2/mutation-checks.txt`). |
| The listener is on for every integration test, asserted by a fixture test | yes | **Every integration suite `make test-int` runs** (the 6 nx `test-int` projects, all of which import pymongo/`onb_data`) loads the shared plugin `libs/py-data/src/onb_data/safety_net_pytest.py`. Its conftest imports `pytest_configure` (installs the listener before any client), the autouse `tenant_safety_net_guard` (wraps each test in `listener.guard()`), `pytest_sessionfinish` (fails the run on leftovers), `tenant_safety_net` and `unscoped`. The conftests are `libs/{py-data,py-eventbus,py-crypto,py-telemetry}/tests_int/conftest.py` and `apps/{api,mcp-mongo}/tests_int/conftest.py`. `tools/tests_int` is the compose stack check: it never opens a Mongo client, has no nx `test-int` target, and is out of scope.<br>**Fixture tests, one per suite:**<br>• `libs/py-data/tests_int/test_safety_net.py::test_every_integration_test_runs_under_the_safety_net_guard` (+ `…registered_globally_before_any_client`, `…client_made_inside_a_test_reports_to_the_listener`)<br>• `libs/py-eventbus/tests_int/test_safety_net_guard.py::test_every_event_bus_integration_test_runs_under_the_safety_net_guard`<br>• `libs/py-crypto/tests_int/test_safety_net_guard.py::test_every_py_crypto_integration_test_runs_under_the_safety_net_guard`<br>• `libs/py-telemetry/tests_int/test_safety_net_guard.py::test_every_py_telemetry_integration_test_runs_under_the_safety_net_guard`<br>• `apps/api/tests_int/test_safety_net_guard.py::test_every_api_integration_test_runs_under_the_safety_net_guard`<br>• `apps/mcp-mongo/tests_int/test_safety_net_guard.py::test_every_mcp_mongo_integration_test_runs_under_the_safety_net_guard`<br>Each suite also has `test_the_listener_is_registered_globally`.<br>**It stays true for new suites:** `tools/tests/test_safety_net_suites.py::test_every_mongo_integration_suite_runs_under_the_safety_net` (in `make check`) fails for any `test-int` suite that imports pymongo or `onb_data` without all five plugin names and a fixture test. `…test_the_check_sees_the_known_mongo_suites` keeps the detector from passing vacuously, and 3 more tests drive it on synthetic suites.<br>**The plugin really fails tests:** `libs/py-data/tests/test_safety_net_pytest.py` runs throwaway pytest sessions in a subprocess. A test sending an unscoped command ends `1 passed, 1 error` with exit 1. A scoped one passes. The guard is autouse. `unscoped(reason)` passes. A violation after the last test fails the run. The listener is installed before any test.<br>**Mutants** (`mutation-checks.txt`): M3 (guard not autouse), M4 (not registered), M5 (no post-check), P1 (py-crypto conftest drops the plugin), P2 (py-telemetry drops the guard), P3 (session-end no longer fails), P4 (apps/api drops the plugin) and P5 (apps/mcp-mongo drops the plugin) are each caught by a named test.<br>When first switched on, the guard caught real unscoped root read-backs: 17 in T-1/F-2 (`first-guarded-run.txt`), every root read-back in EVT-1's suite (`eventbus-first-guarded-run.txt`), PII-1's two `org_keys` reads (found by goal-verifier attempt 2), and in attempt 4 every root action in the api, mcp-mongo and PII-4 suites, all traced to test code. It flagged none of the worker's own commands. |
| An index check in `make test-int` fails on a tenant index not prefixed with `org_id` (seen once) | yes | `libs/py-data/tests_int/test_tenant_indexes.py::test_every_tenant_index_on_the_server_starts_with_org_id` walks every non-system database and every collection, including time-series ones. The check passes each index's `unique` flag. Seen once, re-run at db2b119 (`docs/evidence/T-2/index-check-seen-once.txt`, script `seen_once.sh`): I planted `seen_once_t2.hires {email_hash: 1}`. `make test-int` then gave exit 2 with `FAILED …test_every_tenant_index_on_the_server_starts_with_org_id`: `seen_once_t2.hires index email_only: index on tenant collection hires does not start with org_id`. After dropping it, `make test-int` gave exit 0 for 4 projects. EVT-1's real queue index (`status, consumer, available_at`, non-unique) passes the same server-wide walk under ADR 0002 §3. A unique queue index not led by `org_id` is still flagged (`test_a_unique_queue_index_must_still_start_with_org_id`, mutant Q10). |

## The worker-queue allowance (orchestrator request, ADR 0002)
`onb_data/queue_allowance.py::queue_command_allowed` is called from `find_violation` only for tenant
collections. It returns True only for these shapes:

| Command | Collection | Exact shape allowed |
|---------|------------|---------------------|
| `findAndModify` (the claim) | `jobs` only | The filter uses only queue-state fields (`consumer`, `status`, `available_at`, `locked_until`, `attempts`, `max_attempts`) combined with `$or`/`$and`. The update is `$set`/`$unset` of `WORKER_STATE_FIELDS` plus `$inc` of `attempts`. No `remove`, no `upsert`. |
| `update` (leased) | `jobs`, `dead_letters` | Exactly one statement. `q` is exactly `{_id: <scalar>, lease_token: <non-empty str>}`. `u` is a worker-state update. Not multi, not upsert. |
| `delete` (leased) | `jobs`, `dead_letters` | Exactly one statement. `q` is exactly `{_id, lease_token}`, with `limit: 1`. |
| `insert` (orphan dead letter) | `dead_letters` only | Each document either has a string `org_id` (an ordinary dead letter) or is `status: "dead"` with `reason` in {`missing_org_id`, `invalid_org_id`}. |

The orphan dead letter goes beyond the two shapes named in the request. It is the worker's
dead-letter move of a job that has no `org_id`, which PRD §7.9 Event bus and EVT-1's box 5 require,
so I allowed it narrowly rather than make the bus fail. Everything else on the queue collections is
checked as usual and stays flagged when unscoped. That includes `QueueRepository.find` on
`dead_letters` (the operator read), which is not a worker command; see "Found for other goals".

| Proof | Test |
|-------|------|
| The worker's shapes pass (12 cases) | `libs/py-data/tests/test_safety_net_queue.py::test_the_worker_queue_commands_pass[*]` |
| Every other unscoped queue command is flagged (47 cases): claim on `dead_letters` or `hires`, claim by org, `_id` or payload, `$nor`/`$where`, claim moving org, payload or consumer, `$rename`, replacement, pipeline, `remove`/`upsert`; update by `_id` only, extra filter fields, operator `_id`, empty token, multi, upsert, moving org; two-statement updates; delete by `_id` or `limit 0`; find, count, aggregate, distinct; job insert without org; orphan letters for other reasons; leased shapes on non-queue collections | `…::test_every_other_unscoped_queue_command_is_flagged[*]` |
| Real `QueueRepository` traffic passes against Mongo: claim, heartbeat, retry, reclaim, complete | `libs/py-data/tests_int/test_safety_net_queue.py::test_the_worker_queue_cycle_passes_the_net` |
| Orphan claim and dead-letter move in one transaction passes | `…::test_the_orphan_dead_letter_move_passes_the_net` |
| Raw unscoped ops on the queue are still flagged against Mongo (9 cases) | `…::test_any_other_unscoped_queue_command_is_still_flagged[*]`, `…::test_the_operator_dead_letter_read_is_flagged` |
| The real event bus worker passes the net: all 17 EVT-1 integration tests, including the 6-worker concurrency run, crashed lease, exhausted and orphan dead-lettering | `make test-int`, py-eventbus `19 passed` |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `from onb_data.safety_net import install_safety_net`. Call it once, before the first client, to register one global `TenantCommandListener`. Staging app startup should call it there too. | `test_install_registers_one_global_listener`, `test_the_listener_is_registered_globally_before_any_client` |
| PyMongo swallows listener exceptions, so the listener records the violation and logs `tenant safety net: unscoped <cmd> on <db>.<coll>: <reason>` at ERROR (logger `onb_data.safety_net`). `check()`/`guard()` then raise `UnscopedCommandError`. Violations never carry command values. | `test_the_listener_logs_violations_without_values`, `test_the_listener_records_an_unscoped_command_and_check_raises_it` |
| "Scoped" means a top-level `org_id: "<str>"` or `{"$eq": "<str>"}`, or such a branch inside `$and`. Checked: find, count, distinct, findAndModify (filter and update body), update (`q` and `u`), delete, insert, aggregate (leading `$match`, cross-collection stages), database-level `$changeStream`, explain, view create, createIndexes, client bulkWrite, mapReduce. | `tests/test_safety_net.py` |
| The worker queue (`store.queue("jobs"/"dead_letters")`, EVT-1) passes the net as it is. A new kind of cross-org queue command needs a matching shape in `queue_allowance.py` with tests, not a wider net. | the table above |
| Integration tests that read across orgs as root use `with unscoped("reason"):` or, in py-eventbus, `scratch.find/find_one/count`. A new Mongo integration suite imports the five names from `onb_data.safety_net_pytest` in its conftest and adds a fixture test. `make check` fails until it does. `safety_net_pytest` imports pytest, so it is for tests only. | `tools/tests/test_safety_net_suites.py`, `libs/py-data/tests/test_safety_net_pytest.py` |
| `find_index_violation(collection, key, *, unique=False)` and `is_tenant_collection(db, coll)` are public. The `_id` index and non-unique queue indexes are the only tenant indexes that may skip `org_id`. | `test_the_id_index_is_the_only_index_not_led_by_org_id`, `test_a_non_unique_queue_index_may_lead_with_queue_state` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show T-2` lists no ledger rows for T-2 | no |

## Gates
All run at 7d6d922 on compose project `ob-t-2`, after rebasing onto main `1e22bba` (attempt 4).

| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | exit 0. `plan ok`; tooling tests pass, including the 5 in `test_safety_net_suites.py`, which now see 6 Mongo suites; `Successfully ran target lint/typecheck/test for 17 projects`; py-guardrails `210 passed` (`docs/evidence/T-2/make-check.txt`). |
| Check, py-data alone | `nx run-many -t lint typecheck test -p py-data` | exit 0. `361 passed`, `TOTAL 801 6 99%`. `safety_net.py` and `queue_allowance.py` are at 100%; `safety_net_pytest.py`'s fixture bodies run in the subprocess sessions. |
| Integration | `make test-int` | exit 0. Stack `12 passed`; py-telemetry `5`; api `10` (8 + 2 guard); py-data `49`; mcp-mongo `79` (77 + 2 guard); py-crypto `12` (PII-1 and PII-4 tests + 2 guard); py-eventbus `19`; `Successfully ran target test-int for 6 projects` (`docs/evidence/T-2/make-test-int.txt`). |
| Contracts | `make contracts-check` | exit 0. `contracts-check: openapi.json and the TS client are up to date` (`docs/evidence/T-2/make-contracts-check.txt`). This diff changes no API model or route. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | Repository filters `{**query, org_id}`, pipelines start `$match {org_id}`, upserts `$setOnInsert {org_id}` | yes. No false positives across the T-1 suite. |
| EVT-1 | `QueueRepository` sends only claim, `{_id, lease_token}` writes and dead-letter insert/find (ADR 0002) | yes. Every worker command in the 17 EVT-1 integration tests passes the allowance; only test root reads were flagged (`eventbus-first-guarded-run.txt`). |
| EVT-1 | ADR 0002 §3, the queue index exception | yes. The net and the index walk match `store.ensure_index`. |
| PII-3 | The redaction fix on main | yes. py-guardrails is green in `make check`. |

## Mutation checks
`docs/evidence/T-2/mutation-checks.txt`, made by `docs/evidence/T-2/mutate.py`, re-run in full on the attempt-4 tree. Each mutant runs 8 suites: py-data unit, and the py-data, py-eventbus, py-crypto, py-telemetry, api and mcp-mongo integration suites, plus `tools/tests/test_safety_net_suites.py`. **All 27 mutants were caught**, and the last line reads `worktree after all mutations: clean`. P4 and P5 (attempt 4) make the api or mcp-mongo conftest drop the plugin. Each fails that suite's fixture test and the tools check.

Attempt 3, suite wiring:
- P1: py-crypto's conftest drops the plugin. `test_every_py_crypto_integration_test_runs_under_the_safety_net_guard` and `tools … test_every_mongo_integration_suite_runs_under_the_safety_net` FAILED.
- P2: py-telemetry loads the plugin without the guard. `test_every_py_telemetry_integration_test_runs_under_the_safety_net_guard` and the tools check FAILED.
- P3: session end no longer fails the run. `test_a_violation_after_the_last_test_fails_the_run` and `test_the_session_hook_in_process` FAILED.

Attempt 1 guards, re-run after the rebase:
- M1: every filter counts as scoped. 61 unit and 10 int failures, including `test_an_unscoped_find_through_a_leaked_client_trips_the_listener`.
- M2: the listener records nothing. 14 int failures, including the leaked-client test.
- M3: the guard is not autouse. `test_every_integration_test_runs_under_the_safety_net_guard` FAILED.
- M4: the listener is not registered globally. `test_the_listener_is_registered_globally_before_any_client` and 14 more FAILED.
- M5: no post-check. `test_the_guard_fails_on_a_violation_sent_inside_it` FAILED.
- M6: the index check is off. `test_the_index_check_catches_a_tenant_index_not_led_by_org_id` and 5 unit tests FAILED.
- M7 to M12: org operators, cross-collection stages, allowance reset, update bodies, change streams and explain. Each was caught by unit cases.

Queue allowance:
- Q1: allowance removed. 11 unit failures, 4 py-data int errors, and **10 py-eventbus integration tests error**, so the real bus needs exactly this.
- Q2: allowance on any tenant collection. 2 unit cases, the leased shapes on `hires`.
- Q3: claim allowed on `dead_letters`. Unit `[claim on dead_letters]` and int `[claim a dead letter]`.
- Q4: claim may filter on any field. 5 unit cases (`aimed at an org`, `by id`, `searching payloads`, …).
- Q5: worker update may write any field. 6 unit cases and int `[move a job to another org]`.
- Q6: leased write with any filter. 5 unit cases and int `[update a job by id only]`.
- Q7: leased delete may remove many. Unit `[delete leased many]`.
- Q8: multi/upsert leased update. Unit `[update leased multi]`, `[update leased upsert]`.
- Q9: orphan letter for any reason. 3 unit cases and int `[orphan dead letter for another reason]`.
- Q10: index exception extends to unique. `test_a_unique_queue_index_must_still_start_with_org_id` and the createIndexes case.

RED before GREEN:
- `red-unit-tests.txt`
- `red-int-tests.txt`
- `red-review-fixes.txt` (17 failed)
- `red-queue-allowance.txt` (15 failed before `queue_allowance.py` existed)

## Verifier
goal-verifier attempt 3: **pass**. Attempt 1 passed, and attempt 2 failed box 2 (py-crypto's suite was not guarded).

**For integration.** Main has moved to `1e22bba` (MCP-3, PII-4), past this branch's base `707b60c`. In the verifier's trial merge, nothing conflicted, but `tools/tests/test_safety_net_suites.py` (in `make check`) then reports `apps/api/tests_int` and `apps/mcp-mongo/tests_int`. Both have `test-int` targets and talk to Mongo, but neither loads `onb_data.safety_net_pytest` nor has a fixture test. The new py-crypto Mongo tests (`test_grants_mongo.py`, `test_vault_mongo.py`) have not yet run under the guard. The check is doing its job. The fix is to rebase onto current main, wire the plugin and a fixture test into those two suites, and mark any deliberate root read-backs. Following the hold instruction, I have not rebased; it needs the orchestrator's go-ahead.

## Review findings left open
Attempt 1 ran a code review and a security review. Every high and medium finding was fixed except the one listed first below.

- **medium · `safety_net.py:find_violation` · Admin and introspection commands on tenant collections pass:** `drop`, `dropIndexes`, `collMod`, `renameCollection`, `collStats`, `dataSize`, `validate` and `listIndexes`. They are not filter-bearing queries. A default-deny allowlist needs the real set of commands migrations use, so I suggest it for DEP-1/staging.
- **low · a non-simple `collation` can widen an `org_id` equality to case variants.** Org ids should be generated in one case (AUTH-1/org creation).
- **low · `allowing_unscoped`:** an asyncio task created inside the block stays exempt (documented). Nothing stops application code from calling it; recorded as a lint rule.
- **low (goal-verifier, attempt 1) · `guard()` has no `try/finally`.** A test that raises reports its violation at the next test or at session end. Left as is: a `finally` check would replace the test's own exception.
- **low (attempt 2) · the queue allowance mirrors `QueueRepository`'s field sets** by importing `CLAIM_FILTER_FIELDS` and `WORKER_STATE_FIELDS` from `onb_data.queue`. Widening those sets widens both together. That is intended (one source of truth), but a change there should be reviewed as a net change too.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal T-2`.
- Staging wiring (`install_safety_net()` at app startup) is left to the API/DEP goals.

## Found for other goals
- **EVT-1 / ops:** `QueueRepository.find` on `dead_letters` (the operator read across orgs) is flagged by the net, because it is not a worker command and the request allowed only the worker's. Nothing calls it today. An ops/admin goal that needs it should add a named shape (with a limit and audit) or read per org.
- **API goals / DEP-1:** call `install_safety_net()` at startup in staging, and alert on the `onb_data.safety_net` ERROR line.
- **Every future Mongo integration suite** (apps/api, workers, MCP Mongo) imports the plugin in its conftest and adds the fixture test. `make check` enforces this.
- **py-telemetry's integration tests** now import `onb_data` (the plugin) without a declared dev dependency. It resolves because the root uv workspace installs every member. If py-telemetry is ever tested in isolation, add `onb-data` as a dev dependency (a `uv.lock` change I left out of this goal).
- **Lint rule:** forbid `allowing_unscoped` and `pymongo` imports outside `libs/py-data` and the test suites.
- **Worktrees:** after rebasing onto a main with new npm devDependencies (C-1's `openapi-typescript`), run `make bootstrap` before `make check`.

## Decisions forced (ADRs written)
None needed a new ADR. The allowance implements ADR 0002 "Consequences" as written.
- **"Fails any command" with a listener.** PyMongo swallows listener exceptions, so tests fail through the guard fixture and staging alerts on the ERROR log line.
- **Global registration**, so raw and leaked clients are covered.
- **Allowance by shape in the library**, not a `with unscoped(...)` block in the bus. Shape matching keeps production staging traffic quiet without any bypass in application code, and keeps the net blind to nothing but those exact commands.
- **Changed test files are not weakened.** Root read-backs in the T-1, F-2 and EVT-1 integration tests now run in a named `unscoped(...)` block, or through `Scratch.find/find_one/count`, with identical assertions. F-2's counts add the `org_id` every inserted document carries. The one deliberate orphan-job plant in EVT-1's test has its own named block.

## Traps hit
- Listener exceptions are swallowed by PyMongo.
- `--import-mode=importlib` means guard logic must live in the library (`listener.guard()`) to be unit- and mutation-tested.
- A random hex database name contained `b1`, the test's "value never echoed" sentinel. Fixed with a non-hex sentinel.
- After the rebase, `make check` needed `make bootstrap` for C-1's new npm devDependency.
- The session's shell hook refuses heredocs and complex compound commands. Scripts live in the job temp directory or `docs/evidence/T-2/`.
