# Handoff — RUN-1 · Start a run that compiles the DAG and executes it in LangGraph        attempt 2 · goal/run-1-orchestrator-core · rebased on main fdccbce · evidence captured at 6305267 (see `git log -1` for the handoff commit)

**Outcome claimed.** `POST /runs` (HR, HR_ADMIN) starts a run on a pinned, published template version. It compiles the DAG with DAG-1 and recomputes the hash before persisting. In one EVT-1 outbox transaction it writes the run, one `run_tasks` row per compiled task, the `run.start` audit record and `run.started` (key `run:<id>:started`).

`apps/orchestrator`'s `orchestrator.start_run` consumer executes the run as a LangGraph `StateGraph`:
- one node per task; edges from `depends_on`; parallel fan-out; fan-in joins;
- a tenant-scoped Mongo checkpointer on `thread_id = org_id:run_id`;
- an FR-R2 state machine for every task move;
- a stub executor (the agents arrive in RUN-3).

Gates ran on the compose stack `ob-run-1` (Mongo 8 replica set, Caddy 19600/19601). Evidence is in `docs/evidence/RUN-1/`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| In a diamond DAG, B and C run in parallel (overlapping timestamps) and D starts only after both (test) | yes | **Unit** (`box-tests.txt`, `81 passed`):<br>• `apps/orchestrator/tests/test_run_graph.py::test_in_a_diamond_b_and_c_run_in_parallel_and_d_starts_only_after_both`: stub tasks take 0.15 s. It asserts B's and C's `started_at`/`finished_at` intervals in `run_tasks` overlap, both start after A finished, and `D.started_at >= max(B, C).finished_at`.<br>• `::test_a_join_waits_for_every_branch_even_a_longer_one`: `a→b→b2→d`, `a→c→d`; D starts after the longer branch and runs once.<br>• `::test_the_join_waits_for_the_slower_branch`.<br>**Integration** (`make-test-int.txt`, `orchestrator:test-int 9 passed`): `apps/orchestrator/tests_int/test_runs_against_mongo.py::test_a_diamond_started_through_the_outbox_runs_b_and_c_in_parallel_and_d_after_both`. The run is started through the real `MongoEventBus` in a transaction and claimed by a real worker (0.3 s tasks). The same overlap and order assertions hold on the stored timestamps; the run is `completed`, and its single job is `done` in its org.<br>**Mutations:** M1 (join replaced by two plain edges) and M2 (`max_concurrency: 1`) are each killed. |
| Skipped dependencies don't block; illegal state transitions are rejected (tests) | yes | **Skipped:**<br>• `test_run_graph.py::test_a_skipped_dependency_does_not_block_its_dependent`: a sales hire skips `e`; `f` (which depends on `e`) runs and is `done`; `e` never executes.<br>• `libs/py-agents/tests/test_run_states.py::test_skipped_dependencies_do_not_block`, and `test_a_dependency_that_is_not_done_or_skipped_blocks[*]`.<br>**Illegal transitions:**<br>• `test_run_states.py::test_illegal_state_transitions_are_rejected[*]` covers all 52 non-FR-R2 pairs and `test_the_transitions_that_matter_most_are_illegal[*]` covers the ones that matter most (pending→running, done→running, skipped→ready...).<br>• `test_run_graph.py::test_an_illegal_transition_is_rejected_and_nothing_is_written`: `RunStore.move_task` raises and the task stays `pending`.<br>• `test_run_store.py::test_a_task_moves_only_along_the_state_machine_and_stamps_its_times` and `::test_a_move_that_loses_a_race_is_a_conflict_and_writes_nothing` (compare-and-set).<br>• `test_run_graph.py::test_a_task_whose_dependency_is_not_finished_is_refused`: the runner re-checks FR-R3 itself.<br>**Mutations:** M3–M8 are each killed. |
| The checkpoint `thread_id` is `org_id:run_id` (test) | yes | **Unit:** `test_run_graph.py::test_the_checkpoint_thread_id_is_org_id_colon_run_id`: every `run_checkpoints` and `run_checkpoint_writes` document has `thread_id == f"{org}:{run_id}"` and `org_id == org`.<br>**Checkpointer:** `test_checkpointer.py::test_a_thread_that_is_not_org_id_colon_run_id_of_the_tenant_is_refused[*]` covers another org's thread, a bare run id and malformed ids, each refused before any I/O.<br>**Integration:** `tests_int::test_the_checkpoint_thread_id_is_org_id_colon_run_id_and_every_checkpoint_carries_the_org` and `::test_two_orgs_runs_execute_each_in_its_own_org`.<br>**Mutations:** M9 (bare run id as the thread), M10 (another org's thread accepted) and M11 are each killed. |
| A run stays pinned to its version after a newer version is published (test) | yes | • **Through TPL-2's real lifecycle and routes:** `apps/api/tests/test_runs_route.py::test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published`. v1 is published and a run started; v2 adds `exit_survey` and is published; v2 and v1 runs are then started. The first run's stored `template_version` stays 1, its `dag_hash` equals the later v1 run's, and `exit_survey` appears in neither its tasks nor its stored DAG. The v2 run has `exit_survey` and a different hash.<br>• **Executed:** `test_run_graph.py::test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published`. The v1 run executes after v2 exists; it runs exactly v1's tasks (no `g`) and its hash equals `compile_dag(v1).hash`.<br>• **On the replica set:** `tests_int::test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published`.<br>• **Integrity:** `test_run_records.py::test_the_hash_is_recomputed_before_persisting_and_a_changed_dag_is_refused` and `test_run_graph.py::test_a_run_whose_stored_dag_was_changed_is_not_executed`.<br>**Mutations:** M12, M13 and M14 are each killed. |
| `make test-xt` covers `POST /runs` | yes | `make check` (`make-check.txt`): `cross-tenant surface: 28 API routes, 9 MCP tools, 2 event consumers`, `256 passed`, `make check: all passed`. The new cases:<br>• **`ROUTE_CASES[("POST", "/runs")]`** (`tools/tests_xt/test_xt_api.py`):<br>&nbsp;&nbsp;– org B's hire, a key only org B has, and a version only org B has reached each answer exactly like a missing one (404, `assert_indistinguishable`);<br>&nbsp;&nbsp;– no org-B document changes (root snapshot);<br>&nbsp;&nbsp;– the three 404s write nothing in org A (+1 run and +1 `run.start` audit, for the 201 only);<br>&nbsp;&nbsp;– the started run, its tasks, event and job are all org A's, and `thread_id == org_acme:<id>`;<br>&nbsp;&nbsp;– the generic checks: expected statuses (HR/HR_ADMIN 201, MANAGER 403), no org-B markers, and naming org B in the query, headers or body changes nothing or is refused with 422.<br>• **Consumer:** `surface.PLATFORM_SUBSCRIPTIONS = {"runs": onb_agents.runs.SUBSCRIPTIONS}`, `BUS_MODULES = {libs/py-agents/src/onb_agents/runs/events.py}`, and `CONSUMER_CASES[orchestrator.start_run]` runs the real handler on each org's seeded run (`world.XT_RUNS`).<br>• **Own consumer checks:** `test_an_org_a_job_naming_org_bs_run_changes_nothing_and_starts_nothing` (the confused-deputy case) and `test_the_orchestrator_completes_an_org_a_run_entirely_in_org_a`.<br>• **Coverage tests:** `test_every_api_route_has_a_cross_tenant_case` and the consumer coverage test pass. |

RED before GREEN: every new test module was run first and failed at collection (missing module) or on the assertion. Examples are in the session log for `test_run_states.py`, `test_checkpointer.py`, `test_runs_route.py`, `test_run_failures.py`, and `test_a_planted_type_in_a_checkpoint_blob_is_never_built` (1 failed before the strict serializer).

## Attempt 2: rebase onto main fdccbce
The orchestrator authorised the rebase after HIRE-2, SEED-1, UI-8, NOTIF-1 and UI-4 merged. Conflicts were in `apps/api/src/onb_api/app.py`, `main.py`, `apps/api/tests/conftest.py`, `test_role_matrix.py` and `tools/tests_xt/{conftest,surface,test_xt_api,test_xt_consumers}.py`. Every conflict kept **both** sides; no goal's lines were dropped:
- **`create_app`:** `documents`, `evals`, `llm`, `notify` (main) and then `runs` (this goal), in the parameter list, lifespan, `app.state` and routers. The module docstring covers all of them.
- **`main.py`:** UI-8's `llm_config` and NOTIF-1's `TELEGRAM_WEBHOOK_SECRET` / `MockChannels(..., BusDecisions(store))` stay; `run_bus` and `RunService` are added after them.
- **API tests:** the role-matrix rows of HIRE-2, UI-8, NOTIF-1 and UI-4 stay alongside `POST /runs`. The conftest fixtures (`documents`, `notify_db`, `decisions`, `notify`) stay alongside `run_outbox` and `runs`.
- **xt suite:** `PLATFORM_SUBSCRIPTIONS = {"notify": NOTIFY_SUBSCRIPTIONS, "runs": RUN_SUBSCRIPTIONS}`; `BUS_MODULES` holds NOTIF-1's two modules plus `onb_agents/runs/events.py`; `CONSUMER_CASES` has both `notify.in_app` and `orchestrator.start_run`; NOTIF-1's `test_the_in_app_consumer_never_tells_another_orgs_user` is kept whole next to this goal's two consumer tests.
- **Generated files:** `libs/ts-api-client/{openapi.json,schema.ts}` auto-merged and `make contracts-check` confirms they are current; `uv.lock` is unchanged by the merge.

No behaviour of this goal changed in the rebase. `make check`, `make test-int`, `make contracts-check` and `make evals-selftest` were re-run on the rebased branch (Gates above), and the mutation script was re-run (below).

**Ops note, from the rebase.** The verifier's earlier crashed `make evals-selftest` run had left a scratch database `onb_eval_*` (seeded synthetic eval fixtures, 260 KB, 2 orgs and 5 hires) on this worktree's own `ob-run-1` Mongo volume, which made `py-evals:test-int` fail its cleanup assertion. I inspected it and dropped that one database, then re-ran. It touched no other stack.

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **States** (`onb_agents.runs`):<br>• `TaskState` covers FR-R2 (`pending, ready, running, waiting_approval, done, failed, skipped, cancelled`); `RunState` is `started, running, completed, failed`.<br>• `check_transition` / `check_run_transition` raise `IllegalTransitionError`.<br>• `dependencies_met` counts `done` or `skipped`.<br>• Failed is terminal; RUN-2 extends the tables for retry and `blocked`.<br>• The values equal `onb_contracts.runs.TaskStatus` / `RunStatus`. | `test_run_states.py`, `test_the_states_are_the_api_contracts_states` |
| **Records:** a run is `onboarding_runs {_id: run_<24hex>, hire_id, template_key, template_version, dag_hash, dag (the compiled document), thread_id, status, traceparent, created_by, created_at, updated_at}`. Each task is `run_tasks {_id: <run>:<task>, run_id, task_id, position, title, type, agent, action, depends_on, status, skip_reason, due, sla_hours, attempts, ready_at, started_at, finished_at}`. No PII: only `HIRE_VARIABLES` (role, location, start_date, manager_id) reach the compiler. `stored_dag(run)` refuses a DAG whose hash changed. | `test_run_records.py` |
| **Store:** `RunStore`:<br>• `move_task` / `move_run` are compare-and-set; a lost race raises `TransitionConflictError` and writes nothing.<br>• `cancel_unfinished(run_id, at=)` cancels every unfinished task.<br>• The unique index is `(org_id, run_id, task_id)`. | `test_run_store.py`, `test_run_failures.py` |
| **Starting a run:** `RunStarter.start(template=, hire_id=, variables=, actor_id=)` compiles and writes the run, its tasks, the audit record and the event in one outbox `change`. A `CompileError` writes nothing. A failed audit write leaves no run, task, event or job (replica set). | `test_run_store.py`, `tests_int::test_a_start_whose_audit_write_fails_leaves_no_run_task_event_or_job` |
| **The bus:** `run_bus(store)` is the platform's only `MongoEventBus`. `SUBSCRIPTIONS = {"run.started": ("orchestrator.start_run",)}`. New consumers are added here and get a `CONSUMER_CASES` row. | `test_the_run_bus_subscribes_the_orchestrator_to_run_started`, xt coverage |
| **API:** `POST /runs` (`startRun`) takes `RunCreate {hire_id, template_key, template_version}` (every field required, unknown fields 422) and returns 201 `RunEnvelope`.<br>• A foreign or missing hire, key or version is 404, identically.<br>• A version that does not compile for the hire, archived versions included, is 422 with `details[0].issue = "<reason>: <message>"` and no hire values.<br>• MANAGER is 403; with no run service the route answers 503.<br>• `create_app(..., runs=RunService)` is the last argument. `RunService.build(store, bus, auditor=, hires=, templates=, bus_indexes=bus.ensure_indexes)`. | `test_runs_route.py`, `test_role_matrix.py` |
| **Orchestrator:** `onb_orchestrator.consumer.start_run_handler(store, executor)`:<br>• It is idempotent (finished runs are left alone, a run with a checkpoint resumes, done tasks are skipped).<br>• It never executes a task another delivery has `running` (transient `TaskInProgressError`).<br>• A failed task, a changed DAG or any deterministic error makes the run `failed` and cancels every unfinished task.<br>• Database errors and compare-and-set races are re-raised for the bus to retry.<br>• It drives only `started` / `running` runs.<br>• `TaskExecutor.execute(TaskWork)` is the seam for RUN-3. The worker is `python -m onb_orchestrator` (`MONGO_URI`, `MONGO_DATABASE`, optional `ORCHESTRATOR_STUB_DELAY`, fails fast). | `test_run_graph.py`, `test_run_failures.py`, `test_worker_main.py`, `tests_int` |
| **Checkpointer:** `TenantCheckpointSaver(store)` is a LangGraph `BaseCheckpointSaver` on tenant repositories (`run_checkpoints`, `run_checkpoint_writes`, unique `org_id`-first indexes).<br>• The thread must be `<tenant org>:<run_id>`.<br>• Loads use the strict msgpack allowlist: a planted type loads as plain data and is never built.<br>• It is async only; `alist(None)` is refused; there is no `adelete_thread` yet.<br>• Node errors reach it as their type only. | `test_checkpointer.py`, `test_a_node_error_reaches_the_checkpoint_as_its_type_only` |
| **Tracing:** task spans `orchestrator.task` continue from the `traceparent` stored on the run, with `org_id`, `run_id`, `task_id` and `agent_name` attributes. | `runner.py` (F-4's `continue_from`); no dedicated span test (see Left undone) |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U3 @ MS-6 | compose, 2026-11-12 | contributes: HR picks a published template version for a hire and the run starts and executes (stub executor) | `box-tests.txt`, `make-test-int.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check (incl. test-xt) | `make check` | exit 0, `make check: all passed`, on the rebased branch.<br>• `NX Successfully ran target lint / typecheck / test for 17 projects`; py-agents `676 passed`, `TOTAL 2351 55 98%`; api `679 passed`.<br>• test-xt `256 passed`, `cross-tenant surface: 28 API routes, 9 MCP tools, 2 event consumers` (NOTIF-1's `notify.in_app` and this goal's `orchestrator.start_run`); mcp-hub test-xt `13 passed`.<br>Evidence: `make-check.txt`. |
| Integration | `make test-int` | exit 0, `NX Successfully ran target test-int for 10 projects`: orchestrator `9 passed`, api `44`, py-eventbus `19`, py-data `50`, mcp-hub `33`, mcp-mocks `11`, py-evals `4` (`make-test-int.txt`). The first run's one failure (py-evals' cleanup assertion) was the leftover scratch database below; after it was dropped, the whole target was re-run clean. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (`make-contracts-check.txt`) |
| Eval self-test | `make evals-selftest` | `selftest evr_20261112T111308Z_4bf7bec3: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS` (`make-evals-selftest.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| EVT-1 | `publish(..., change=fn)` writes change, event and jobs atomically; a duplicate key writes nothing; workers set the tenant context; delivery is at least once | yes. The audit-failure rollback and duplicate-key tests on the replica set; the redelivery test. |
| DAG-1 | `compile_dag` is pure; skipped tasks stay with a reason; `CompiledDag.hash_of(document(include_hash=False)) == dag.hash`; `CompileError(reason)` names no values | yes. The hash is recomputed before persisting (M13), and `str(CompileError)` already starts with `<reason>:` (now used as-is). |
| TPL-2 | `TemplateLifecycle.get_version(key, n)` returns a `Template` with `version` and `status`; archived versions say `archived`; bodies never change | yes. An archived version gives 422 `not_published`; pinning holds through publish v2. |
| HIRE-1 | `HireService.store.get(id)` is tenant-scoped; the hire document holds role, location, start_date and manager_id, with no raw values | yes. Only those four fields are read. |
| T-3 | `ROUTE_CASES`, `PLATFORM_SUBSCRIPTIONS`, `BUS_MODULES`, `CONSUMER_CASES`, and `world.tenant_documents` drives the count and marker expectations | yes. The seeded runs went into `tenant_documents` (a side seed broke the count tests). |
| F-4 | `inject_traceparent()` / `continue_from()` | yes |
| T-2 | the safety net on Mongo suites | yes. The new `apps/orchestrator/tests_int` carries the plugin and the guard test; it is added to `KNOWN_MONGO_SUITES`. |

## Mutation checks
`docs/evidence/RUN-1/mutation-checks.txt` (script `mutate.py`): **21 of 21 killed**, `source diff after all mutations: 0 files differ`, exit 0. Each mutation with some of the tests it failed:
- **M1** join replaced by two plain edges → `test_a_join_waits_for_every_branch_even_a_longer_one`
- **M2** `max_concurrency: 1` → `test_in_a_diamond_b_and_c_run_in_parallel_and_d_starts_only_after_both`
- **M3** skipped no longer counts as finished → `test_a_skipped_dependency_does_not_block_its_dependent`, `test_skipped_dependencies_do_not_block`
- **M4** runner dependency re-check removed → `test_a_task_whose_dependency_is_not_finished_is_refused`
- **M5** pending→running allowed, **M6** done→running allowed → `test_illegal_state_transitions_are_rejected[*]`, `test_the_transitions_that_matter_most_are_illegal[*]`, `test_an_illegal_transition_is_rejected_and_nothing_is_written`
- **M7** compare-and-set removed → `test_a_move_that_loses_a_race_is_a_conflict_and_writes_nothing`
- **M8** skipped tasks executed → `test_a_skipped_dependency_does_not_block_its_dependent`
- **M9** bare run id as the thread → `test_the_checkpoint_thread_id_is_org_id_colon_run_id`
- **M10** another org's thread accepted → `test_a_thread_that_is_not_org_id_colon_run_id_of_the_tenant_is_refused[org_b:run_1]`
- **M11** replayed regular writes overwrite → `test_pending_writes_come_back_in_order_and_are_written_once`
- **M12** route ignores the requested version → `test_a_run_stays_pinned_to_its_version_after_a_newer_version_is_published` (API)
- **M13** no hash recompute before persisting → `test_the_hash_is_recomputed_before_persisting_and_a_changed_dag_is_refused`
- **M14** stored DAG not checked → `test_a_run_whose_stored_dag_was_changed_is_not_executed`
- **M15** consumer drives runs it does not own → `test_a_run_in_a_state_this_orchestrator_does_not_drive_is_left_alone[*]`
- **M16** MANAGER may start a run → `test_a_manager_may_not_start_a_run`, `test_role_matrix[POST /runs-MANAGER]`
- **M17** failed run keeps unfinished tasks → `test_a_failed_task_cancels_its_cut_off_sibling_and_every_task_after_it`
- **M18** a running task executed again → `test_a_task_another_delivery_is_running_is_not_executed_again`, `test_overlapping_deliveries_never_run_a_task_twice…`
- **M19** deterministic errors retried → `test_a_blocked_task_fails_the_run_instead_of_retrying_forever`
- **M20** permissive msgpack hook → `test_a_planted_type_in_a_checkpoint_blob_is_never_built`
- **M21** node errors keep their message → `test_a_node_error_reaches_the_checkpoint_as_its_type_only`

## Review findings left open
Two independent reviews were run on `git diff main...HEAD` (general-purpose agents: code and security). No CRITICAL findings. These were fixed in `941d3f5`, each with a test and a mutation:
- **HIGH** a sibling cut off by LangGraph stayed `running` (M17)
- **MEDIUM** deterministic errors left the run stuck (M19)
- **MEDIUM** permissive checkpoint deserialization (M20)
- **MEDIUM** a running task replayed (M18)
- **LOW** overlapping deliveries racing on the run move
- **LOW** node errors stored with their message (M21)
- **LOW** the 422 reason repeated
- **LOW** the confused-deputy consumer case and the org-A write counts in xt
- **LOW** the runner↔graph import cycle (the consumer moved to `consumer.py`)

Left open:
- **MEDIUM · `apps/api/src/onb_api/runs/service.py` · `POST /runs` is not idempotent over HTTP.** A client retry starts a second run for the same hire, and nothing stops two active runs per hire. The PRD does not require it, and choosing between an `Idempotency-Key` header and a partial unique index on active runs per hire is a product decision → **RUN-2 / SEC-2** (with rate limiting).
- **LOW · `runner.py` · A task left `running` by a crashed worker is never taken over.** It raises `TaskInProgressError` (transient) until the job dead-letters, and the run stays `running`. Safe (no double execution), but recovery needs a per-task lease → **RUN-2** (restart survival).
- **LOW · `checkpointer.py` · No `adelete_thread`/retention; `alist` makes one writes query per checkpoint** → RUN-2 and ops.
- **LOW · the DAG hash is unkeyed SHA-256.** It catches drift, not tampering by someone with database write access (that also needs the strict serializer, now done). A keyed MAC belongs with SEC-1.
- **LOW · a client-sent `traceparent` header becomes the run's stored trace.** That is correlation only, with no data. It belongs with OBS-1's ingress policy.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session. I worked to the text printed by `plan.py goal RUN-1`.
- **There is no compose service for the orchestrator worker.** `python -m onb_orchestrator` exists, but `infra/docker-compose.yml` runs no app services yet (deployment goal).
- **There is no dedicated test that task spans join the request's trace.** The code uses F-4's `continue_from` with the run's stored traceparent. This is not a Done-when box; RUN-2/OBS-1 can add a span assertion.
- **Approval tasks run through the stub like action tasks.** `interrupt()` and `waiting_approval` belong to the HIL goal.

## Found for other goals
- **RUN-2:**
  - add retry and `blocked` to `onb_agents.runs.states` (failed→ready and the run states);
  - add a per-task lease, so a crashed `running` task is taken over;
  - add a graceful worker drain;
  - add `GET /runs`, `GET /runs/{id}` and the controls, with ROUTE_CASES rows;
  - add HTTP idempotency for `POST /runs`;
  - add thread retention (`adelete_thread`).
- **RUN-3:** implement `TaskExecutor.execute(TaskWork)` with the registered agents. A task executes at most once per attempt (`TaskWork.attempt`), so agents need no replay handling. Keep executor errors type-only (the runner already reduces them).
- **HIL:** turn approval tasks into `interrupt()` and `waiting_approval`. The runner raises `TaskBlockedError` on `waiting_approval`; resume with `Command(resume=…)` on the same `thread_id`.
- **HUB-1 (flake):** `apps/mcp-hub/tests_int/test_hub_xt.py::world` uses `secrets.token_hex(6)` as a marker. About 0.4% of random markers hold a 10-digit run that the hub's raw-PII scanner refuses (`docs/evidence/RUN-1/hub-marker-flake-probe.txt`: 86 of 20000 (0.43%) in the recorded run, e.g. `906227260088`; an earlier run gave 73). It failed once in this session's first `make test-int` and passed on the re-run. Fix: a letter-prefixed marker.
- **Ops / test infra:** the `ob-run-1` Mongo container restarted once mid-`make test-xt` (exit 0, not OOM; four stacks share a 7.7 GiB Docker VM). That gave `ServerSelectionTimeoutError` setup errors in two runs. The `make check` run that produced the evidence was clean.
- **Deployment:** the orchestrator worker service needs `MONGO_URI`, `MONGO_DATABASE` and `OTEL_*`, and `LANGGRAPH_STRICT_MSGPACK=true` as defence in depth. Its DB role needs read/write on `onboarding_runs`, `run_tasks`, `run_checkpoints`, `run_checkpoint_writes`, `jobs`, `dead_letters` and `events`.

## Decisions forced (ADRs written)
- **ADR 0015 — run execution and the tenant checkpointer** (`docs/adr/0015-run-execution-and-tenant-checkpointer.md`).
- **PRD conflict, reported rather than built around silently.** PRD §7.1 names `langgraph-checkpoint-mongodb`. That library writes checkpoints through its own client, filtered by `thread_id` only and without `org_id`, which contradicts §7.9 (all access through `TenantRepository`, checkpoints carry `org_id`, the T-2 safety net). The ADR records the choice of a `BaseCheckpointSaver` on tenant repositories with the same storage model and LangGraph's serializer. **Haneef:** if you want the library itself, §7.9 needs an exception for the checkpoint collections.
- **Shared run code lives in `onb_agents.runs`,** used by both the API and the orchestrator. `run_bus` is the single bus constructor.
- **Failure semantics:** a failed run cancels its unfinished tasks; deterministic errors fail the run; transient ones retry. A `running` task is never re-executed by another delivery.

## Traps hit
- **LangGraph supersteps are barriers.** A symmetric diamond cannot tell a fan-in join from two plain edges; only an uneven branch can (M1 needs `test_a_join_waits_for_every_branch_even_a_longer_one`).
- **LangGraph cancels in-flight siblings when one node fails.** `CancelledError` is not an `Exception`, so the sibling's record stayed `running` (review HIGH).
- **The default `JsonPlusSerializer` builds any type a msgpack blob names** unless `allowed_msgpack_modules=None` (or `LANGGRAPH_STRICT_MSGPACK`).
- **T-3's count tests derive their expectations from `world.tenant_documents`.** Extra seeded documents must go there, not in a side seed.
- **`rtk` condenses `make` output.** Evidence files were captured with `rtk proxy make …`.
- **The session shell refuses complex heredocs and compound git commands.** Patches were applied as scripts from the job temp directory.
