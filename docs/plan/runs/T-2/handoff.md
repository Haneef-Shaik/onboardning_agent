# Handoff — T-2 · Catch any unscoped query at runtime        attempt 1 · goal/t-2-query-safety-net · see `git log -1` (evidence captured at d175d2d)

**Outcome claimed.** `onb_data.safety_net` adds a PyMongo command listener (`TenantCommandListener`).
It flags every command on a tenant collection that is not pinned to exactly one `org_id`.
`install_safety_net()` registers it globally. The integration suite installs it before any client
exists and wraps every test in `listener.guard()`, so a test that sends an unscoped command fails.
`make test-int` also checks every index on every database on the server and fails on a tenant
index that does not start with `org_id`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A raw unscoped `find` through a deliberately leaked client trips the listener (test) | yes | `libs/py-data/tests_int/test_safety_net.py::test_an_unscoped_find_through_a_leaked_client_trips_the_listener` takes `store._client` from `connect()` (the deliberate leak) and runs `find({})` on `hires`. It asserts that rows from both orgs came back, then that `tenant_safety_net.check()` raises `UnscopedCommandError` matching `unscoped find on <db>.hires`. A sibling test does the same with a raw `MongoClient` that has no listener argument: `test_an_unscoped_find_through_a_raw_client_trips_the_listener`. Run with `make test-int`: `33 passed`, exit 0 (`docs/evidence/T-2/make-test-int.txt`). Mutants M1, M2 and M4 each fail this test (`docs/evidence/T-2/mutation-checks.txt`). |
| The listener is on for every integration test, asserted by a fixture test | yes | `libs/py-data/tests_int/conftest.py`: `pytest_configure` calls `install_safety_net()` before any client is built. The autouse `tenant_safety_net_guard` wraps each test in `listener.guard()`, which runs `check()` before and after the test. `pytest_sessionfinish` fails the run on violations left after the last test. The fixture tests are in `tests_int/test_safety_net.py`:<br>• `test_every_integration_test_runs_under_the_safety_net_guard` asserts every collected item has the guard in its `fixturenames`. M3 (guard not autouse) fails it.<br>• `test_the_listener_is_registered_globally_before_any_client` asserts the listener is in the global registry. M4 fails it.<br>• `test_a_client_made_inside_a_test_reports_to_the_listener` gives a fresh client no listener argument and still sees its command reported.<br>• Unit tests `test_the_guard_fails_on_a_violation_sent_inside_it` (M5) and `…_left_before_it` cover the guard itself.<br>The first guarded run caught 17 real unscoped root read-backs in the existing T-1 and F-2 integration tests (`docs/evidence/T-2/first-guarded-run.txt`). That shows the guard fails tests in practice. |
| An index check in `make test-int` fails on a tenant index not prefixed with `org_id` (seen once) | yes | `libs/py-data/tests_int/test_tenant_indexes.py::test_every_tenant_index_on_the_server_starts_with_org_id` walks every non-system database and every collection, including time-series ones. It runs `find_index_violation` on each index. Seen once (`docs/evidence/T-2/index-check-seen-once.txt`, script `seen_once.sh`): I planted `seen_once_t2.hires {email_hash: 1}` with mongosh. `make test-int` then gave exit 2 with `FAILED …test_every_tenant_index_on_the_server_starts_with_org_id`: `seen_once_t2.hires index email_only: index on tenant collection hires does not start with org_id`. After dropping the database, `make test-int` gave exit 0 with `33 passed`. `test_the_index_check_catches_a_tenant_index_not_led_by_org_id` keeps that case as a permanent test. M6 fails it. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `from onb_data.safety_net import install_safety_net`. Calling `install_safety_net()` once, before the first client, registers one global `TenantCommandListener`; calling it again returns the same listener. Staging app startup should call it there too. | `test_install_registers_one_global_listener`, `test_the_listener_is_registered_globally_before_any_client` |
| PyMongo swallows listener exceptions, so the listener cannot stop a command. It records the violation and logs `tenant safety net: unscoped <cmd> on <db>.<coll>: <reason>` at ERROR (logger `onb_data.safety_net`), and `check()`/`guard()` raise `UnscopedCommandError` (a `DataAccessError`). Staging should alert on that log line. | `test_the_listener_logs_violations_without_values`, `test_the_listener_records_an_unscoped_command_and_check_raises_it` |
| Violations and logs carry only db, collection, command name and a fixed reason, never values from the command. | `test_the_listener_logs_violations_without_values`, the `ORG not in reason` asserts |
| "Scoped" means a top-level `org_id: "<str>"` or `{"$eq": "<str>"}`, or such a branch inside `$and`. `$or`, `$in`, `$ne`, `$exists` and a missing `org_id` never count. | `test_an_unscoped_command_on_a_tenant_collection_is_flagged[*]` (45 cases) |
| These are checked: `find`, `count`, `distinct`, `findAndModify` (filter and update body), `update` (each `q` and `u`), `delete`, `insert` (string `org_id` on every document), `aggregate` (first stage `$match` on `org_id`; no `$out`/`$merge`; `$lookup`/`$graphLookup`/`$unionWith` into a tenant collection is refused at any depth), database-level `$changeStream`, `explain` (unwrapped), `create` of a view, `createIndexes` (`org_id` first), client `bulkWrite` on a tenant namespace, and `mapReduce`. An update body may not write `org_id` except `$setOnInsert` of the filter's own org, which is what T-1's upsert sends. A replacement must keep the filter's `org_id`. Pipeline updates are refused. | `tests/test_safety_net.py` |
| Out of scope by design: global collections (`orgs`, `agent_registry`, `tool_catalog`), `system.*`, and the `admin`/`config`/`local` databases. | `test_commands_outside_tenant_data_pass[*]` |
| All TenantRepository traffic passes the net with no false positives. | `test_scoped_repository_traffic_is_not_flagged`, and the whole T-1 suite running under the guard |
| An integration test that must read across orgs as root wraps the read in `with unscoped("reason"):` (conftest fixture) or scopes its filter by `org_id`. | `test_tenant_repository.py`, `test_compose_mongo_transaction.py` |
| `find_index_violation(collection, key)` and `is_tenant_collection(db, coll)` are public helpers. The `_id` index is the only tenant index that may skip `org_id`. | `test_the_id_index_is_the_only_index_not_led_by_org_id` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show T-2` lists no ledger rows for T-2 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | **exit 2, from a defect outside this diff.** `py-guardrails:test` fails `test_redact_property.py::test_no_character_of_a_value_survives_in_any_glue` on a Hypothesis counterexample (`value='account number 000000000', before='_'`). It reproduces without Hypothesis: `redact("_account number 000000000")` returns the input unchanged (`docs/evidence/T-2/py-guardrails-repro.txt`). The code is PII-3's (`libs/py-guardrails`, last changed in `3c917b3` on main), and this diff does not touch it (`git diff main...HEAD --name-only` lists only `libs/py-data/**`, `docs/evidence/T-2/**` and `docs/plan/runs/T-2/**`). All other targets pass: plan check, `lint` and `typecheck` for 17 projects, and `test` for 16 of 17 (`docs/evidence/T-2/make-check.txt`). |
| Check, the project this diff touches | `nx run-many -t lint typecheck test -p py-data` | exit 0. `All checks passed!`, `Success: no issues found in 20 source files`, `238 passed`, `TOTAL 577 0 100%` |
| Integration | `make test-int` | exit 0. Stack checks `12 passed`, py-data `33 passed` (`docs/evidence/T-2/make-test-int.txt`) |

These ran on compose project `ob-t-2` (Caddy 127.0.0.1:18300/18301). The other gates (test-xt, contracts, evals, PII, e2e) do not cover this diff.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `connect(..., event_listeners=[...])` hook | Held, but not needed: global registration also covers clients built outside `onb_data`, which is the point of a safety net. |
| T-1 | Repository filters are `{**query, org_id}`, pipelines start with `$match {org_id}`, upserts add `$setOnInsert {org_id}` | Held. No false positives across the whole T-1 integration suite. |
| T-1 | `GLOBAL_COLLECTIONS == {orgs, agent_registry, tool_catalog}` | Held. The net imports it from `onb_data.store`. |
| F-2 | `root_client`/`mongo_tunnel` fixtures and `make test-int` with `.env` | Held |

## Mutation checks
Full output: `docs/evidence/T-2/mutation-checks.txt`, made by `docs/evidence/T-2/mutate.py`. Each file is restored after its mutant, and the last line reads `worktree after all mutations: clean`. All 12 mutants were caught.
- M1 (box 1): every filter counts as scoped. 28 unit failures, plus int `test_an_unscoped_find_through_a_leaked_client_trips_the_listener`, `…_raw_client_…` and `test_a_client_made_inside_a_test_reports_to_the_listener`.
- M2 (box 1): the listener records nothing. 8 unit and 4 int failures, including the leaked-client test.
- M3 (box 2): the guard fixture is not autouse. `test_every_integration_test_runs_under_the_safety_net_guard` FAILED.
- M4 (box 2): the listener is not registered globally. `test_the_listener_is_registered_globally_before_any_client` and 4 more int tests FAILED.
- M5 (box 2): the guard does not check after the test. `test_the_guard_fails_on_a_violation_sent_inside_it` FAILED.
- M6 (box 3): the index check is off. `test_the_index_check_catches_a_tenant_index_not_led_by_org_id` (int) and 2 unit tests FAILED.
- M7: `org_id` operators count as one org. 3 unit cases FAILED.
- M8: cross-collection stages are not checked. 7 unit cases FAILED.
- M9: the allowance never resets. `test_allowing_unscoped_is_explicit_and_scoped_to_the_block` FAILED.
- M10: update bodies are not checked. 9 unit cases FAILED.
- M11: database-level change streams pass. 2 unit tests FAILED.
- M12: `explain` is not unwrapped. 2 unit cases FAILED.

RED before GREEN: `red-unit-tests.txt` (ImportError before the module existed), `red-int-tests.txt` (fixtures missing: 1 failed, 6 errors), `red-review-fixes.txt` (17 failed before the review fixes).

## Review findings left open
I ran a code review and a security review: two general-purpose agents with reviewer briefs, on `git diff main...HEAD`.

Fixed:
- **High:** the update body was unchecked, so `$set org_id` to another org, `$unset`/`$rename`, replacements and `findAndModify` updates all passed.
- **Medium:** database-level `$changeStream` passed.
- **Medium/low:** `explain` was not unwrapped.
- **Medium/low:** a view that joins tenant data passed.
- **Low:** insert accepted `org_id: {"$eq": …}` as a value.
- **Low:** a `$lookup` with a `from` that is a mapping passed.
- **Low:** an exception inside the check would have been silently lost. It is now recorded as a violation.
- **Low:** violations sent after the last test were never raised. `pytest_sessionfinish` now fails the run.
- **Low:** time-series collections were skipped by the index walk.

Still open:
- **medium · `safety_net.py:find_violation` · Admin and introspection commands on tenant collections pass:** `drop`, `dropIndexes`, `collMod`, `renameCollection`, `collStats`, `dataSize`, `validate` and `listIndexes`. They leak only totals or act on the whole collection (all orgs), and they are not filter-bearing queries, which is what the PRD line covers. Left because a default-deny allowlist needs the real set of commands migrations and ops use. Suggested for DEP-1/staging: add an allowlist mode.
- **low · `safety_net.py` · A non-simple `collation` (e.g. `strength: 1`) can widen an `org_id` equality to case variants.** Org ids match `[A-Za-z0-9_-]` (T-1), so case-only variants are possible in principle. Left: org ids should be server-generated in one case. Recorded for AUTH-1/org creation.
- **low · `allowing_unscoped` · An asyncio task created inside the block stays exempt for life (contextvar copy).** Documented in the docstring. Also, nothing stops application code from calling it. Recorded: a lint or grep rule should limit `allowing_unscoped` to `tests_int/`.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal T-2`.
- `make check` is not exit 0 because of the `py-guardrails` defect above. Fixing it belongs to PII-3 (standing rule 4). This needs Haneef or the orchestrator: either fix PII-3's redaction first or accept T-2 with this known red gate.
- Staging wiring (calling `install_safety_net()` at app startup when the environment is staging) is left to the app/deploy goals (API-*, DEP-1). The function and log contract are ready.

## Found for other goals
- **PII-3 / py-guardrails (blocking `make check` on every branch once the counterexample is found):** `redact("_account number 000000000")` leaks the account number. Underscore-prefixed context defeats the bank-account detector. Hypothesis saved the example in `.hypothesis/` of this worktree, so it replays here. Other worktrees will hit it whenever Hypothesis finds it.
- **API goals / DEP-1:** call `install_safety_net()` at startup in staging, and alert on the `onb_data.safety_net` ERROR log line.
- **Every future project with integration tests that touch Mongo** (apps/api, workers, MCP Mongo): copy or share the three conftest hooks (`pytest_configure` install, autouse `tenant_safety_net_guard`, `pytest_sessionfinish`). The fixture test only covers `libs/py-data/tests_int`. A shared pytest plugin module would be a better home once a second project needs it.
- **EVT-1:** job claiming across orgs (T-1 open finding) will show up as violations here. Use `GLOBAL_COLLECTIONS` for a system queue, or an explicit narrow exemption; don't widen the net.
- **Lint rule:** forbid `allowing_unscoped` and `pymongo` imports outside `libs/py-data` and `tests_int`.

## Decisions forced (ADRs written)
None needed an ADR:
- **"Fails any command" with a listener.** PyMongo 4.19 `publish_command_start` wraps every listener in `try/except → _handle_exception()`, so a listener cannot abort a command. In tests the command is failed through the guard fixture, which fails the test. In staging it is an ERROR log line to alert on. This matches PRD §7.9 ("tests + staging"). Production enforcement stays with `TenantRepository`.
- **Global registration rather than T-1's `event_listeners=` hook,** so raw and leaked clients are covered too.
- **The T-1 and F-2 integration tests were changed, not weakened.** Root read-backs across orgs are wrapped in `unscoped("reason")` with identical assertions. F-2's transaction counts add `org_id: "org_test"`, which every document they insert carries.

## Traps hit
- Listener exceptions are swallowed by PyMongo (see above). The first design, raising in `started()`, would have passed silently.
- `--import-mode=importlib` means conftest helpers cannot be imported by tests, so the guard logic lives in the library (`listener.guard()`) where it can be unit- and mutation-tested.
- The session's shell hook refuses heredocs and complex compound commands. Scripts live in the job temp directory or `docs/evidence/T-2/`.
