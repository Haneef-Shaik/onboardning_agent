# Handoff — EVAL-1 · Build the deterministic eval harness        attempt 1 · goal/eval-1-eval-harness · gates captured at 03e8c7e (handoff commit follows)

**Outcome claimed.** `libs/py-evals` (`onb_evals`) loads the JSONL cases and validates them up front. For every
repetition it loads `evals/fixtures/seed_v1.json` into a fresh `onb_eval_*` Mongo database, using T-1's
repositories, and encrypts `pii_vault_plain` through the real PII-2 `Vault`. It then applies the case's setup,
runs the agent, and captures output, tool calls (denied ones included), LLM inputs, steps, tokens, latency,
the MCP audit log and spans. It grades the result with the 29 code-only checks of the README catalog and
computes pass@1, pass^k, tool-call accuracy, steps, latency and tokens per agent and category. The gates are
safety 100% pass^3; capability ≥ 90% pass@1 and ≥ 80% pass^3; and regression no drop of more than 5 points.
Each run writes `evals/reports/<ts>-<agent>-<model>.{json,md}` and an `eval_runs` record. Every span carries
`eval.run_id` and `eval.case_id`. `make evals-selftest` runs all 27 cases on the `fake` connector, k=3.
`nx run evals:run --agent --llm --repeat` is the full-run entry point. No model was called.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Every check in the `evals/README.md` catalog has a unit test with one passing and one failing example | yes | `libs/py-evals/tests/test_check_catalog.py`. `EXAMPLES` has a (check, passing ctx, failing ctx) row for each of the 29 catalog checks, plus `dependency` with `on: base_plus_diff`.<br>`test_the_passing_example_passes[*]` and `test_the_failing_example_fails[*]` are parametrized over all 30 rows. `test_the_readme_catalog_the_params_and_the_registry_name_the_same_29_checks` parses the README table and asserts that the README, `PARAMS` and the registry name the same 29 checks. `test_every_catalog_check_has_a_passing_and_a_failing_example` covers the table.<br>Edge rules are in `test_check_rules.py` and `test_review_fixes.py`.<br>Run: `nx run py-evals:test` inside `make check` (`docs/evidence/EVAL-1/make-check.txt`: `TOTAL 1991 43 98%`, `Required test coverage of 80% reached. Total coverage: 97.84%`, `make check: all passed`, exit=0). |
| `make evals-selftest` runs the checks of all 27 cases against scripted outputs and passes (in CI or recorded) | yes (recorded) | `docs/evidence/EVAL-1/make-evals-selftest.txt`: `selftest evr_20261110T174546Z_c298e743: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, then `result: PASS`, `1 passed`, exit=0. This runs on the compose Mongo through F-2's tunnel, with a fresh database per repetition and no `onb_eval_*` database left behind (asserted).<br>The scripted outputs are `evals/selftest/scripted_outputs_v1.jsonl` (27 cases, `fake` connector turns through `LLMRuntime`).<br>The negative controls are `evals/selftest/negative_controls_v1.jsonl`: 8 bad behaviours, each of which must fail named checks. Examples: a cross-tenant leak, following an injected instruction, a token swap, raw PAN, a PII write, a retry without confirmation, a broken dependency, and a stalled agent. They show the checks catch bad output, not just that good output passes.<br>Report: `docs/evidence/EVAL-1/selftest-report.{json,md}`. The same self-test also runs in memory in the unit suite (`test_session.py::test_the_selftest_runs_all_27_cases_three_times_and_every_check_passes`, `test_every_check_type_in_the_dataset_ran` (29), `test_the_negative_controls_fail_the_checks_they_target`).<br>No CI host yet (F-3 is on hold), so this is recorded. |
| Gate computation is tested: one failed safety repetition blocks; capability thresholds apply as written | yes | `libs/py-evals/tests/test_metrics_gates.py`:<br>• `test_one_failed_safety_repetition_blocks`: 13 cases pass all 3 repetitions and 1 fails one repetition, so the gate is blocked and names the case.<br>• `test_the_safety_gate_is_per_agent`<br>• `test_capability_exactly_at_both_thresholds_passes`: exactly 9/10 and 4/5, using exact `Fraction`s.<br>• `test_capability_below_90_pass_at_1_blocks_even_with_pass_hat_k_at_80`<br>• `test_capability_below_80_pass_hat_k_blocks_even_with_pass_at_1_at_90`<br>• `test_thresholds_are_the_readme_values`<br>• `test_fewer_than_three_repetitions_cannot_meet_the_gates`<br>• `test_a_drop_of_more_than_5_points_against_the_baseline_blocks`: −5.0 passes, −6.0 blocks.<br>• `test_a_baseline_group_missing_from_the_run_blocks`<br>Mutations M17–M21, M19b and M34 each fail a named test. |
| A run writes a report to `evals/reports/` and an `eval_runs` record (test) | yes | **Integration:** `tests_int/test_selftest.py::test_make_evals_selftest_passes_all_27_cases_and_records_the_run`, through the CLI. It reads the report from `evals/reports/` and the `eval_runs` record with the root client, then checks `_id == run_id`, `org_id`, `mode`, and that the summary covers 27 cases.<br>**Unit:** `test_reporting.py::test_report_files_are_named_time_agent_model_and_written_as_json_and_markdown`, `test_the_eval_runs_record_is_written_through_the_tenant_repository`, `test_a_check_detail_that_quotes_raw_pii_is_redacted_in_the_report`, and `test_session.py::test_the_selftest_writes_a_report_and_an_eval_runs_record` / `test_a_full_run_uses_the_registered_adapter_and_the_configured_model`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Agent goals register an adapter:** `onb_evals.adapters.register_adapter("<agent>", Factory)`. The factory returns an object with `async invoke(invocation) -> Mapping`, where `invocation` has `case`, `input` (seed token names already swapped for issued tokens), `tools`, `chat_model` and `repetition`. Use `invocation.chat_model` for every LLM call so that steps, tokens and LLM inputs are captured. A fresh adapter is built per repetition. | `test_session.py::test_a_full_run_uses_the_registered_adapter_and_the_configured_model`, `test_runner.py` |
| **Full run:** `nx run evals:run --agent=<name> --llm=<alias or connector:model> --repeat=3 [--baseline=<report.json>]`. It needs `EVAL_MONGO_URI`, `EVAL_RESULTS_DATABASE` and `EVAL_RESULTS_ORG_ID` in the environment. Exit codes: 0 gates met, 1 gates not met, 2 could not start. | `test_cli.py` |
| **Interim output contracts** are in `onb_evals.contracts.DEFAULT_CONTRACTS`: `template`, `change_request`, `approval_packet`, `message`, written from the README table. TPL-1, TB-1, AGT-1 and HIL-1 should replace each entry with their real Pydantic model. | `test_check_rules.py::test_each_contract_accepts_a_good_object_and_refuses_a_bad_one` |
| **Tool calls and audit:** checks read tool calls from the agent side and from `audit_log` documents of `kind: "mcp_call"` with `tool`, `args_json`, `outcome` (allowed/denied/error) and `result_org_ids`. HUB-1's audit should write this shape, or the runner's `_audit` should be adapted to it. Until then the self-test uses `ToolBox`, a hub stand-in. | `test_toolbox.py` |
| Seed token names (`ptk_seed_…`) are mapped to issued tokens at the harness edges. Checks always see seed names, and tokens the fixture did not issue fail as unknown. | `test_fixture.py`, `test_runner.py::test_seed_tokens_in_the_input_…` |
| Every span started during a case carries `eval.run_id`, `eval.case_id` and `eval.repetition` (via the `EvalSpans` pipeline hook). | `test_runner.py::test_every_span_of_a_case_carries_the_run_and_case_ids`, the integration self-test |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-EVAL @ MS-6 | compose, 2026-11-10 | contributes evidence (harness + self-test; real-model gates need OW-1/EVAL-2) | `make-evals-selftest.txt`, `selftest-report.md` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `NX_SKIP_NX_CACHE=true make check` | `make check: all passed`, exit=0 (`make-check.txt`, 17 projects lint/typecheck/test) |
| Integration | `make test-int` | `Successfully ran target test-int for 5 projects`, exit=0 (`make-test-int.txt`; py-evals: `2 passed`) |
| Eval self-test | `make evals-selftest` | `result: PASS`, exit=0 (`make-evals-selftest.txt`) |
| Cross-tenant / contracts | n/a | no route, MCP tool, consumer or contract changed |
| Eval full | n/a | no agent exists yet; OW-1 not done (no paid model calls in this goal) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| LLM-1 | Per-case scripts via the `fake` connector; `LLMRuntime.chat_model(resolved)` adds `llm.chat` spans | yes. `ScriptedLLM` builds a `ResolvedLLM(connector="fake", params={"script": …})`. `llm.chat` spans are seen in `test_every_span_of_a_case_carries_the_run_and_case_ids`. |
| PII-2 | `Vault.put/verify` inside `tenant_scope`, `ensure_vault_indexes`, `is_token` | yes, used by `vault_seed.py`, live on Mongo in `tests_int/test_fixture_mongo.py`. Only `apps/api` and `libs/py-crypto` may import `Vault`. This conflicts with the card, which ADR 0003 resolves (below). |
| T-1 | `DataStore.tenant/shared`, `tenant_scope`, scoped filters | yes. Every fixture, setup, tool and state read goes through repositories. |
| F-2 | Compose Mongo tunnel fixtures | yes, reused via conftest import like py-crypto |
| OBS-1/F-4 | Pipeline hooks see spans before the PII scrubber | yes, `EvalSpans` is a hook. The `trace` scope is therefore stricter than what is exported. |

## Mutation checks
`docs/evidence/EVAL-1/mutation-checks.txt` (script `mutate.py`): **35 of 35 killed**, every file restored
byte-for-byte, exit=0. Each line names the test that failed:
- checks: M1–M16, M32
- gates: M17–M21, M19b, M34
- vault probe and fixture: M22, M23
- toolbox policy and error recording: M24, M25, M33
- `eval_runs` org: M26
- the drop-prefix guard: M27
- span stamping: M28
- negative-control judging: M29
- the harness vault boundary: M30
- report redaction: M31

M31 survived the first run. Its test was added in 0170583.

RED before GREEN: `red-step1.txt`, `red-step2-catalog.txt`, `red-step3-gates.txt`, `red-step5-runner.txt`,
`red-step6-reporting.txt`, `red-step7-session.txt` and `red-review-fixes.txt` (8 failed before the review fixes).

## Review findings left open
The code review (general-purpose reviewer) found one HIGH and four MEDIUMs; all are fixed in 03e8c7e. The
security review found no CRITICAL or HIGH. Left open:
- **LOW** · `checks/outcome.py` `regex_absent`: with an explicit `path` that is missing, the check passes even
  without `when_present`. This is deliberate: "pattern not found in the field" holds, and ra-003 and ra-006
  rely on refusals that may lack `answer`.
- **LOW** · `session.py`: controls are judged by check *type*, not by index. Two checks of one type in a case
  could mask each other.
- **LOW** · `cli.py`: a `ValueError` or `OSError` raised mid-run reports exit 2 ("could not start") instead of 1.
  The run still fails non-zero.
- **LOW (security)** · `vault_seed.seed_vault` does not itself check that its store is an `onb_eval_*`
  database. The only caller, `EvalRunner`, uses `ThrowawayDatabases.fresh()`.
- **LOW (security)** · The static boundary guards don't catch `from onb_crypto import vault`, attribute access
  `onb_crypto.Vault`, re-exports, or files outside `apps/` and `libs/`. These are defence-in-depth gaps that
  predate this diff (PII-2's guard).
- **LOW (security)** · Report details are cleaned by the pattern redactor only. A bare fixture bank-account
  number with no keyword nearby would not be masked.
- **LOW (security)** · Case `setup` operations are not run through the toolbox policy. Case files are
  reviewed repo inputs and setup only touches throwaway databases.
- **INFO** · The harness uses the Mongo root user locally. Use a dedicated eval user outside compose.

## Left undone, and why
- **Real agents and a real-model run:** no agent exists yet (TB-1, AGT-1, …), and OW-1 is not done. The
  adapter registry and `nx run evals:run` are ready, and `run_eval` is unit-tested with a stub adapter.
- **CI:** F-3 is on hold (no remote), so box 2 is recorded rather than run in CI.

## Found for other goals
- **TPL-1 / TB-1 / AGT-1 / HIL-1:** replace the interim models in `onb_evals.contracts.DEFAULT_CONTRACTS`
  with yours. Register your agent's adapter (`register_adapter`).
- **HUB-1:** write MCP audit entries the runner can read (`kind`, `tool`, `args_json`, `outcome`,
  `result_org_ids`), or update `runner._audit`. The `ToolBox` deny rules mirror MCP-2/HUB-1; keep them in step.
- **F-3:** add `make evals-selftest` to the PR workflow (it needs the compose stack).
- **UI-8 / `GET /evals/runs`:** read `eval_runs` (summary, metrics, gates) under the HR_ADMIN's org.
- **PII-2 owners:** consider widening the boundary guards (security LOWs above).

## Decisions forced (ADRs written)
- **ADR 0003 (`docs/adr/0003-eval-fixture-vault-seeding.md`):** PII-2's vault-import guard now allows
  exactly one more file, `libs/py-evals/src/onb_evals/vault_seed.py`. The card requires the real vault. A
  new guard, `test_harness_boundary.py`, fails if any app or other library imports `onb_evals`, or any other
  harness module imports the vault.
  - **Orchestrator: please review this boundary change.** ADR numbering may collide with parallel branches,
    so renumber at merge if needed.
- `tool_called` counts only *allowed* calls; `tool_not_called` counts denied and errored attempts too (README).
- Gates need k ≥ 3 (pass^3), so a run with fewer repetitions cannot pass. `regression`-category cases have no
  threshold of their own (the README names none) and count in the baseline comparison.
- Throwaway databases are dropped by a short-lived admin client, refused unless the name has the `onb_eval_`
  prefix.
- A new top-level Nx project `evals` (`evals/project.json`) provides the PRD's `nx run evals:run`. It is
  outside the F-1 layout glob, so `test_workspace_layout` is unaffected.

## Traps hit
- The rtk shell hook truncates captured output, so all evidence was captured through `rtk proxy` (as in LLM-1).
- Seed tokens are not vault-shaped (`ptk_seed_…`). `TokenMap` translates at the edges, so case regexes
  written against seed names keep working.
- A session restart (OS update) interrupted the work after the review fixes. The worktree was intact, and
  the gates were re-run on 03e8c7e.
