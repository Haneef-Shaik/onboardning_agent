# Handoff — RUN-3 · Run task nodes through registered agents with scoped tokens and grants        attempt 1 · goal/run-3-agents-in-runs · base main fcc6ab9 (see `git log -1` for the handoff commit)

**Outcome claimed.** Each task node of a run runs its registered agent (AG-1's runtime, per-task agent token), gets a PII grant only when its template task declares `pii_access` (exactly its fields and sink, the run's own `run_id`), has its model recorded once on the task, fails visibly when the per-run token budget is exhausted, and its spans form run → task → agent → LLM/tool. A sink's `task.sink_rejected` fails the task. Design: `docs/adr/0019-agent-tasks-in-runs.md`.

Gates ran on compose stack `ob-run-3` (ports 19500/19501). No real model is called: LLM-1's `fake` connector throughout. Evidence is in `docs/evidence/RUN-3/`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A task without `pii_access` gets no grant; a task with it gets a grant limited to its fields and sink (tests) | yes | `apps/orchestrator/tests/test_agent_executor.py` (`box-tests.txt`, `37 passed` with the span and sink tests):<br>• `test_a_task_without_pii_access_gets_no_grant`: `AgentTask.grant is None`, and the issuer is never called.<br>• `test_a_task_with_pii_access_gets_a_grant_limited_to_its_fields_and_sink`: the grant verifies (`GrantVerifier`); `fields == {mobile}`, `sink == notify.telegram`, org, run, task and subject (hire) as expected.<br>• `test_only_the_task_that_declares_pii_access_gets_a_grant_in_a_run` (before/after tasks get none), `test_the_grant_is_issued_with_the_runs_own_run_id` (NOTIF-1 `wrong_run`), `test_each_task_gets_its_own_agent_token_scoped_to_its_agent`, `test_the_grant_and_the_token_are_never_stored` (DB scan).<br>• Fail closed: `test_a_field_the_sink_does_not_resolve_…`, `test_a_field_that_is_not_a_vault_field_…`, `test_pii_access_without_a_grant_issuer_fails_closed` (task failed, agent never started, no grant).<br>**Real Mongo:** `tests_int/test_agents_against_mongo.py::test_only_the_task_declaring_pii_access_gets_a_grant_for_its_fields_and_sink`, `::test_each_task_runs_its_agent_…` (also scans for any JWS-shaped string stored). `make-test-int.txt`: `orchestrator:test-int` ran, `NX Successfully ran target test-int for 10 projects`.<br>**Mutations:** M1–M6 killed. |
| The model is recorded on the task and doesn't change when config changes mid-run (test) | yes | • `test_the_model_is_recorded_on_the_task`: `llm == {connector: fake, model: fake-standard, alias: standard, source: manifest}`.<br>• `test_a_config_change_while_the_task_runs_does_not_change_its_recorded_model`: the config is swapped while the agent runs; the record keeps `fake-standard`.<br>• `test_a_new_attempt_of_a_task_keeps_its_model_and_a_new_task_takes_the_new_config`: after a config change a second attempt rebuilds the recorded model (`pinned_from == [recorded]`, no new resolution), the record is unchanged, and a task started after the change gets `fake-standard-v2`.<br>• `libs/py-agents/tests/test_run_store.py::test_a_tasks_model_is_recorded_once_and_never_overwritten` (store level).<br>• Real Mongo: `$exists` update, in `tests_int::test_each_task_runs_its_agent_…` (`llm` on both tasks).<br>**Mutations:** M7 (not recorded), M8 (re-resolved on a new attempt), M9 (overwritten) killed. |
| An exhausted per-run token budget fails the task visibly (test) | yes | • `test_an_exhausted_run_token_budget_fails_the_task_visibly`: run budget 1000, tasks use 600 each: `one` done, `two` `failed` with `failure == {kind: budget_exceeded, reason: "run tokens 1200/1000"}` and `result.outcome == failed`, `three` cancelled, run `failed`.<br>• `test_the_run_budget_counts_tokens_across_parallel_tasks`, `test_the_run_budget_is_rebuilt_from_the_tasks_recorded_usage_after_a_restart` (asserts the exact reason `run tokens 1200/1000` from a fresh executor), `test_a_task_that_fits_the_run_budget_records_its_usage`.<br>• Real Mongo: `tests_int::test_an_exhausted_run_token_budget_fails_the_task_and_the_run`.<br>**Mutations:** M10 (budget not shared), M11 (not passed to the runtime), M12 (restart forgets usage), M13 (failure not raised), M14 (cause not stored) killed. |
| A run's spans form run → task → agent → LLM/tool (in-memory exporter tree test) | yes | `apps/orchestrator/tests/test_run_spans.py::test_a_runs_spans_form_run_task_agent_llm_and_tool`: in-memory exporter, diamond run, every agent calls a bound tool. One `orchestrator.run`; four `orchestrator.task` children of it (parallel branches included); four `agent.run`, each the child of its own task span; eight `llm.chat` whose ancestry is exactly `guardrails.invoke → agent.run → orchestrator.task → orchestrator.run`; four `tool.call` children of `agent.run`; one trace id throughout. Also `test_the_run_span_joins_the_trace_of_the_request_that_started_the_run` and `test_spans_carry_ids_only`.<br>**Mutations:** M16–M19 killed. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `AgentTaskExecutor(store, agents, models, tools, auditor, token_key, grants, run_max_tokens, external=None)` implements RUN-1's `TaskExecutor`. It reads the task from the run's hash-checked DAG (so `TaskWork` is unchanged), looks the agent up (`no_agent` / `retired_agent` fail the task), then runs `AgentRunner`. Failures are recorded on the task as `failure = {kind, reason}` (codes only); any non-database error becomes `kind=error` with the exception type; a `PyMongoError` is re-raised for the bus to retry. | `test_agent_executor.py`, ADR 0019 |
| Task records gain `llm` (set once), `result` (`outcome, output, failure, refusal, llm, usage, trajectory_steps`; no trajectory) and `failure`. `RunStore` gains `record_model`, `record_outcome(..., only_if=)`, `fail_running`, `run_tokens`. Outcome writes use `only_if=running`, so a task failed from outside keeps its cause. | `test_run_store.py`, `test_sink_rejected.py::test_an_agents_own_failure_never_overwrites_the_sinks_cause` |
| `orchestrator.fail_task` (`onb_agents.runs.FAIL_TASK` = `onb_contracts.notify.SINK_REJECTED_CONSUMER`) consumes `task.sink_rejected`: fails the payload's `running` task (or, with no `task_id`, every running task of that agent in the run) atomically, with the sink's reason code on the task; finished tasks, other orgs' runs and malformed payloads change nothing. It is in **both** `run_bus` `SUBSCRIPTIONS` and `NOTIFY_SUBSCRIPTIONS` (the sink publishes on the latter's bus). The runner ends a task failed from outside `failed`, never `done`. | `test_sink_rejected.py` (10 tests), `tests_xt` row + `test_an_org_a_rejection_naming_org_bs_running_task_fails_nothing_in_org_b`, `tests_int::test_a_sinks_rejection_published_on_the_bus_fails_the_running_task` |
| Worker: `ORCHESTRATOR_EXECUTOR=agents` selects the agents (needs `CONFIG_DIR`, `AGENT_TOKEN_KEY`, `MCP_HUB_URL`, `PII_GRANT_SIGNING_KEY`; optional `PII_SINK_POLICY_FILE`, `ORCHESTRATOR_RUN_MAX_TOKENS`, default 200 000); unset keeps the stub. Startup fails fast naming the variable. `prepare()` runs both consumers. | `test_agents_setup.py` |
| Span names: `orchestrator.run`, `orchestrator.task`, `agent.run` (`AgentRunner.run`), `tool.call` (per tool call). | `test_run_spans.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U7, NF-OBS, SM-4 @ MS-6 | compose, 2026-11-12 | contributes: grants/tokens per task (U7), span tree (NF-OBS), model recorded and budgets (SM-4) | `box-tests.txt`, `make-test-int.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | exit 0 on the final tree (`make-check.txt`; Nx replays cached projects, so its log is short). |
| Integration | `make test-int` | exit 0, `NX Successfully ran target test-int for 10 projects`; `orchestrator:test-int` includes the 4 new Mongo tests (`make-test-int.txt`). |
| Cross-tenant | `make test-xt` | exit 0, `cross-tenant surface: 28 API routes, 9 MCP tools, 3 event consumers` (`make-test-xt.txt`). One earlier run under load showed `257 passed, 1 error` (the known Mongo setup flake); the re-run is clean. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Eval self-test | `make evals-selftest` | `selftest evr_20261112T121230Z_dbaa92e4: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS`. The real eval run needs Bedrock (OW-1), not available. |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-1 | `TaskExecutor.execute(TaskWork)` is the seam; a task's work runs at most once per attempt; `run_bus` is the only bus builder | yes. `TaskWork` untouched; the `fail_task` consumer went into `run_bus`'s map and has an xt case. |
| AG-1 | `AgentRunner.run(agent, AgentTask, run_budget=)`; the grant travels only as a header; `task_record()` for the model | yes. The grant and token are never stored (DB-scan tests). |
| PII-4 | `GrantIssuer.issue_grant(org, run_id, task_id, subject_id, fields, sink, ttl)` checks the sink policy | yes. Refusals fail the task before the agent starts. |
| NOTIF-1 | The sink refuses a grant for another run (`wrong_run`); `task.sink_rejected {run_id, agent, task_id?, sink, reason}` | yes (grant `run_id` = the run's own; consumer written). The sink's own tests are NOTIF-1's. |
| HUB-1 | Scoped agent tokens | yes, via AG-1's `issue_task_token` (`test_each_task_gets_its_own_agent_token…`). |
| MOCK-1 | Fake/mocks path, no real model | yes: LLM-1 `fake` connector; the hub is a `ToolSource` double (the hub itself is AG-1's/HUB-1's). |

## Mutation checks
`docs/evidence/RUN-3/mutation-checks.txt` (script `mutate.py`): **28 of 28 killed**, `source diff after all mutations: 0 files differ`, exit 0. Mutations: grant for every task / for none / wrong sink / wrong run id / refusal ignored / no issuer ignored (M1–M6); model not recorded / re-resolved / overwritten (M7–M9); run budget not shared / not passed / forgotten on restart / failure not raised / cause not stored (M10–M14); retired agent runs (M15); run, task, agent and tool spans (M16–M19); `fail_task` touches finished tasks / does nothing / late success turns a failed task done / outcome clobbers a sink's cause / fails other agents' tasks (M20–M24); subscriptions in the run bus, the sink's bus and the worker (M25–M27); unknown executor value (M28). Three first-run survivors (M8, M9, M10, M26) were equivalent or untested mutants; tests were strengthened (no re-resolution assert, store-level model test, parallel-budget mutant on the real sharing line, contracts test in the mutation set).

## Review findings left open
Two independent reviews (general-purpose agents: code, security) on `git diff main...HEAD`. No CRITICAL. Fixed in `fix(RUN-3): review fixes…`, each with a test:
- **HIGH** an agent's own failure overwrote a sink's cause → `record_outcome(only_if=running)`, `test_an_agents_own_failure_never_overwrites_the_sinks_cause`, M23.
- **HIGH/MEDIUM** `fail_task` stamped a failure on a task that finished between its read and move → one atomic `RunStore.fail_running`, M20/M21.
- **MEDIUM** executor errors other than `AgentTaskFailedError` left a failed task with no cause → recorded as `kind=error` by type; database errors still retry.
- **LOW** the grant was issued before two DB round trips → issued last. Budget rebuild flag made explicit. Restart test asserts the exact reason.

Left open:
- **MEDIUM · `agent_executor.py` · the run budget is per process and counts a task's tokens only when it finishes.** A second worker, or a crash, can overshoot the cap. Recorded in ADR 0019; needs per-call usage recording → RUN-2 / ops.
- **MEDIUM · `consumer.py` · a rejection without `task_id` fails every running task of that agent in the run** (fail closed, one run only). Better if the sink always sends `task_id` (it holds the grant) → NOTIF-1 follow-up / SEC-2. Anyone with event-bus write access can fail running tasks of their own org: the bus is a trust boundary (SEC-2).
- **LOW** the grant is issued even if the agent's manifest cannot reach the sink's tool (useless, not exploitable); no revocation within its lifetime (≤ timeout + 30 s, single use at the sink); the stored `result.output` is the agent's schema-validated output (a PII detector on free-text output fields belongs with SEC-1/GR-1); `AGENT_TOKEN_KEY` strength is length-only; the weak `failed >= 1` parallel-budget assertion.

## Left undone, and why
- `/goal` could not be set from this orchestrated session; I worked to the text `plan.py goal RUN-3` printed.
- No compose service for the orchestrator worker (DEP-1), so `ORCHESTRATOR_EXECUTOR=agents` is tested through settings and `build_agent_executor` on the fake connector, not as a running container.
- `needs_human` answers complete the task with the answer stored; turning them into `waiting_approval` is HIL-1.
- A retried task keeps a stale `failure` until RUN-2 clears it on re-entering `running`.

## Found for other goals
- **RUN-2:** clear `failure` when a task re-enters `running`; record per-call token usage so several workers share the run budget; recover a run whose driver died after `fail_task` failed a task.
- **NOTIF-1 / SEC-2:** have the sink always send `task_id`; treat bus write access as a trust boundary.
- **HIL-1:** `needs_human` → `waiting_approval`.
- **DEP-1:** an orchestrator service with `ORCHESTRATOR_EXECUTOR=agents`, `AGENT_TOKEN_KEY` (same as the hub's), `PII_GRANT_SIGNING_KEY`, `MCP_HUB_URL`, `CONFIG_DIR`, the LLM variables.
- **AGT-1:** agents' `needs_human`/`refused` answers are stored on the task, not failed.

## Decisions forced (ADRs written)
- `docs/adr/0019-agent-tasks-in-runs.md`. Also, to subscribe the consumer where its publisher can see it, `onb_contracts.notify` gained `SINK_REJECTED_CONSUMER` and `NOTIFY_SUBSCRIPTIONS` a third entry. Four tests that asserted exact subscription/consumer lists were updated for it: `libs/py-agents/tests/test_run_store.py`, `libs/py-contracts/tests/test_notify_contracts.py`, `apps/mcp-mocks/tests/test_notify_main.py`, `apps/orchestrator/tests/test_worker_main.py`.

## Traps hit
- The guard's token count is `max(reported usage, prompt chars / 4)`, so budget tests set usage well above the prompt estimate (600).
- A pytest `tests` package per project: py-agents and orchestrator tests cannot be run in one pytest call (module name clash); Nx runs them apart.
- Two eval/xt runs under load hit the known Mongo setup flake; re-run clean.
