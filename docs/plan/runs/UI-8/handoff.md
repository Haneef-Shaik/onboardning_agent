# Handoff — UI-8 · Admin views: agents, denials, audit, PII access, evals        attempt 2 · goal/ui-8-admin-ui · rebased onto main ec3dd2a (TPL-2, AG-1, HIRE-1, MOCK-1) · see `git log -1`

**Attempt 2 (rebase).** Attempt 1 passed the verifier at d0f22de. The orchestrator asked for a rebase onto
main after TPL-2, AG-1, HIRE-1 and MOCK-1 merged. Conflicts were resolved keeping both sides everywhere:
`create_app` has `hires=`, `templates=`, `evals=`, `llm=` (each list: params, lifespan `ensure_indexes`,
`app.state`, routers); `onb_api.main` builds `templates` and `eval_runs`; the API test `conftest` app
fixture takes `audit_log` and `templates` (TPL-2) and the eval store (UI-8); `test_role_matrix` keeps all
nine TPL-2 rows and five TPL-2 sample paths plus the two eval rows and sample; `tools/tests_xt` keeps
`template_drafts`/`_seed_templates`/`template_lifecycle` and every TPL-2 `ROUTE_CASES` row (UI-8's rows now
use main's `_reads_only(...)` wrapper); `test_auth_settings`/`test_telemetry` set both `CONFIG_DIR` and the
LLM env. One change beyond conflict resolution: `onb_api.main` now reads `llm.yaml` from TPL-2's required
`CONFIG_DIR` instead of the working directory (ADR 0014 updated). `apps/web` was untouched on main, so
`App.tsx`, nav and `serve_api.py` merged cleanly. `make contracts` after the rebase produced no diff. All
gates and the Playwright suite were re-run on the rebased branch; mutations re-run: 13 of 13 caught.

**Outcome claimed.** HR_ADMIN has a read-only Admin section (`/admin/*`): every agent with the connector and model it resolves to, each agent's page with its allowed MCP tools, collections, local tools, approvals, limits and its recent denials from the audit log, the audit log (filters, paging), the PII access log (masked only) and eval runs with their gates, metrics and cases. New API routes `GET /evals/runs` and `GET /evals/runs/{run_id}` (HR_ADMIN, tenant-scoped, cross-tenant tested); agent records gain `resolved_llm`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Playwright: HR_ADMIN opens each admin view and sees seeded data | yes | `apps/web/e2e/admin.spec.ts` › `HR_ADMIN opens each admin view and sees seeded data`: signs in through the form, opens Agents (all six registry agents; `db_agent` row shows `e2e-standard-model`), Audit log (`pii.reveal`, `hire.create`, three `agent:db_agent` rows; the `denied` filter leaves 3), PII access log (the seeded reveal: `pan`, `user:u_acme_hr1`, `payroll setup`), Eval runs (both runs newest first; one opened: gate detail, `83.3%` pass@1, cases `3/3`, `2/3`), and asserts no raw seeded value and no `ptk_` anywhere in the page. Data comes from the real API (`apps/web/e2e/serve_api.py` + `admin_seed.py`: registry synced from `config/agents/`, hub-shaped denials in the audit log, a hire created and revealed through `HireService`, eval runs in the harness's stored shape). Command: `E2E_API_PORT=19531 E2E_WEB_PORT=19532 npx playwright test --config apps/web/playwright.config.ts` → `9 passed (6.6s)` (`docs/evidence/UI-8/playwright.txt`); through Caddy on the `ob-ui-8` stack with `E2E_BASE_URL=https://localhost:19501` → `9 passed (8.2s)` (`docs/evidence/UI-8/playwright-via-caddy.txt`). |
| HR and MANAGER get 403 on every admin route (test) | yes | API: `apps/api/tests/test_role_matrix.py::test_role_matrix` rows for `/agents`, `/agents/{name}`, `/audit`, `/pii-access-log`, `/evals/runs`, `/evals/runs/{run_id}` × HR/MANAGER → 403 (HR_ADMIN 200; the sample run is seeded); `test_evals_route.py::test_eval_runs_are_hr_admin_only[*]` and `::test_hr_and_managers_are_refused_before_the_store_is_read` (a spy store records no read). `uv run --frozen pytest apps/api/tests/test_role_matrix.py -k "test_role_matrix and (HR or MANAGER) and (audit or agents or evals or pii-access-log)" -v` → `18 passed`; eval/agent route tests → `14 passed` (`docs/evidence/UI-8/admin-403.txt`). End to end: `admin.spec.ts` › `u_acme_hr1@example.com …` and `u_acme_mgr_ravi@example.com is refused every admin view and every admin route`: no Admin link, "Not available" on five `/admin/*` paths with no admin API call, and a direct `GET /api/<route>` with that user's token is 403 `FORBIDDEN` for all six admin routes (`playwright.txt`). Vitest: `AdminSection.test.tsx` › `refuses %s every admin view without calling an admin route`. |
| An agent's page shows its connector, model, allowed tools and recent denials from the audit log (Playwright) | yes | `admin.spec.ts` › `an agent's page shows its connector, model, allowed tools and recent denials from the audit log`: `agent-connector` = `fake`, `agent-model` = `e2e-standard-model` (resolved from the manifest's `alias: standard` through `config/llm.yaml`), the mongo row lists `find, aggregate, count, update_one` and `run_tasks, documents_meta`, local tool `date_calc`, exactly three denials newest first (`tool:mocks.provision_laptop`, `tool:mongo.update_one/new_hires`, `tool:mongo.find/other`; reason `server_not_in_manifest` on the first; `run_assistant`'s allowed call excluded), and the page asked `GET /audit?action=mcp.tool_call&outcome=denied&actor_type=agent&actor_id=db_agent&limit=10`. `9 passed` (`playwright.txt`, `playwright-via-caddy.txt`). |
| `make test-xt` covers the eval routes | yes | `tools/tests_xt/test_xt_api.py` `ROUTE_CASES` rows `("GET", "/evals/runs")` and `("GET", "/evals/runs/{run_id}")` with their own checks (org A sees only its runs newest first; filtering on org B's agent equals an unknown agent; a shared agent name returns only A's run; paging past A finds nothing; B's admin sees only B's; every B run id is 404 exactly like a missing id, and A's id is 404 to B) plus the generic leak/steering checks per role: 14 collected tests. Org B holds more runs than A (`world.EVAL_RUNS`), written by the harness's own `onb_evals.results.record_run`. `tools/tests_xt/test_xt_eval_records.py` (3 tests) holds the API's test helper, collection name and run-id pattern to the harness. `make test-xt` → `213 passed in 345.08s`, the surface listing includes `route GET /evals/runs` and `route GET /evals/runs/{run_id}`, hub `12 passed`, `exit 0` (`docs/evidence/UI-8/make-test-xt.txt` lines 77–78, 105, 141). One earlier standalone run on the loaded machine hit a Mongo tunnel `ServerSelectionTimeoutError` in the setup of a HIRE-1 case (`212 passed, 1 error`, not an assertion); the same suite passed 213/213 inside `make check` before it and on the re-run. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `GET /evals/runs?agent=&page=&limit=` (≤100, default 20, ≤10 000 deep) → `EvalRunSummary[]` newest first with `meta.page`; `GET /evals/runs/{run_id}` → `EvalRun` (summary + `metrics[agent][category]`, `gates`, `cases`). HR_ADMIN only; other orgs' runs are 404; ids must match `EVAL_RUN_ID_PATTERN` (422). TS client: `listEvalRuns`, `getEvalRun`. | `test_evals_route.py`, ROUTE_CASES |
| `create_app(..., evals: EvalRunsReader \| None, llm: LLMConfig \| None)` (after `templates=`). Without `evals`, `/evals/runs*` answer 503; without `llm`, `resolved_llm` is null. `onb_api.main` wires `EvalRunStore(store)` and `load_llm_config(config_dir / "llm.yaml", os.environ)` (`CONFIG_DIR`, TPL-2) and fails at startup with `LLMConfigError` when it does not resolve. | `test_without_an_eval_store_the_routes_answer_503`, `test_auth_settings.py::test_startup_fails_when_the_llm_configuration_does_not_resolve` |
| `AgentRecord.resolved_llm: {connector, model, alias, source} \| null` (required field, nullable). Never provider params. An alias llm.yaml lacks makes only that agent null (logged), not the list a 500. | `test_agents_route.py` (4 new tests), `test_agent_contracts.py::test_the_resolved_model_is_required_nullable_and_carries_no_provider_params` |
| Recent denials for an agent = `GET /audit?actor_type=agent&actor_id=<name>&action=mcp.tool_call&outcome=denied&limit=10` (no new route; ADR 0014). | Playwright box 3 |
| Test-only `onb_api.testing_evals` (`eval_report`, `stored_eval_run`, `store_eval_run`) writes the stored `eval_runs` shape; apps must not import `onb_evals` (py-evals boundary test). | `tools/tests_xt/test_xt_eval_records.py::test_the_test_helper_stores_exactly_what_the_harness_stores` |
| Web: `apps/web/src/admin/` (AdminSection with sub-nav, `adminApi.ts` fetchers incl. `Paged<T>` with `meta.page`, `parts.tsx` Loaded/Table/Pager/Badge, `AdminClientProvider`/`useAdminClient`). `App.tsx` adds a `RequireSection` wrapper and `<Route path="/admin/*">` before the catch-all `Guarded`, and passes `client` to `AppRoutes`. | `AdminSection.test.tsx` (8 tests), Playwright |
| e2e API (`serve_api.py`) now serves audit, agents, hires, evals and llm from `admin_seed.py` (in memory, synthetic). | Playwright |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U8 — HR_ADMIN sees which agent may access which MCP tools (REG-1, HUB-1, UI-8 @ MS-5) | compose (`ob-ui-8`, Caddy 19501), 2026-11-11 | pass | `docs/evidence/UI-8/playwright-via-caddy.txt` (box 1 and box 3 tests) | yes — UI-8 is the last prover |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed` (line 1197); api `462 passed` (1026), web Vitest `70 passed` (959), cross-tenant `213 passed` (1161) (`docs/evidence/UI-8/make-check.txt`) |
| Cross-tenant | `make test-xt` | `213 passed`, hub `12 passed`, `exit 0` (`make-test-xt.txt`) |
| Integration | `make test-int` | `Successfully ran target test-int for 8 projects`, `exit 0` (`make-test-int.txt`) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (both generated files committed) |
| Playwright | `npx playwright test --config apps/web/playwright.config.ts` direct and via Caddy | `9 passed` both |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

`make evals-selftest` not run: `libs/py-evals` is unchanged.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| UI-1 | Nav table drives nav and guard; `createAppClient`; `MaskedField`; Playwright signs in through the form, moves with pushState | yes |
| AUD-1 | `GET /audit` filters exact-match, newest first, `meta.page`; `listAudit` | yes |
| REG-1 | `GET /agents`, `/agents/{name}`; `RegisteredAgent.manifest.llm` has `LLMSelection`'s shape | yes |
| HUB-1 | Denials are audit records actor `agent:<name>`, action `mcp.tool_call`, outcome `denied`, reason = code | yes (seeded in that shape) |
| HIRE-1 | `GET /pii-access-log` entries with `masked` and `token`; `HireService.build`/`create`/`reveal` | yes |
| EVAL-1 | `eval_runs` records written by `record_run` under the results org | yes; read through `TenantRepository` |

## Mutation checks
`uv run --frozen python docs/evidence/UI-8/mutate.py` → `docs/evidence/UI-8/mutation-checks.txt`, `SUMMARY: 13 of 13 mutations caught`; every file restored (clean `git status` after).
- M1 eval routes allow HR → `test_eval_runs_are_hr_admin_only[hr-…]`, `test_hr_and_managers_are_refused_before_the_store_is_read`, `test_role_matrix[GET /evals/runs-HR]` FAILED.
- M2 foreign run answers 403 → `test_another_orgs_run_is_not_found_like_a_missing_one` FAILED.
- M3 agent filter ignored → `test_the_list_filters_by_agent_and_pages` FAILED.
- M4 oldest first → `test_hr_admin_lists_the_orgs_runs_newest_first` FAILED.
- M5 no resolved model → `test_each_agent_shows_the_connector_and_model_it_resolves_to` FAILED.
- M6 provider params appended to the model → `test_provider_params_never_reach_the_response` FAILED.
- M12 alias error not caught → `test_an_alias_llm_yaml_lacks_leaves_that_agent_unresolved` FAILED.
- M13 an API test imports the harness → `test_harness_boundary.py::test_no_app_or_other_library_imports_the_harness` FAILED.
- M7 denials query without `outcome=denied` → Vitest `shows an agent's connector, model, allowed tools and recent denials…` FAIL.
- M8 `RequireSection` lets any role in → Vitest `refuses … every admin view…` (HR, MANAGER) and `App › refuses a section the role does not have` FAIL.
- M9 PII view renders the token → Vitest `shows the PII access log masked, and never the vault token` FAIL.
- M10 denials card removed → Playwright `an agent's page shows … recent denials…` ✘.
- M11 resolved model ignored in the list → Playwright `HR_ADMIN opens each admin view and sees seeded data` ✘.

## Review findings left open
Code review and security review (general-purpose agents with reviewer briefs, on `git diff main...HEAD`): no critical or high. Fixed: a manifest alias llm.yaml lacks no longer 500s `GET /agents` (both reviewers); metrics formatted (`avg_steps` 1 decimal, p95 rounded).
- medium · `apps/api/src/onb_api/evals/store.py` `page()` · one malformed stored run makes the list 500 (pinned by `test_a_malformed_stored_run_is_an_internal_error_not_a_partial_answer`). Kept: "never shown partially", like REG-1's registry and HIRE-1's access log; the harness is the only writer.
- medium · `apps/web/src/admin/parts.tsx` `Loaded` + `Pager` · when a page-N fetch fails the pager disappears with the table; the user leaves and re-enters the view. UX only.
- low · gate `detail` is redacted by the harness at write time only (`report.py` `redact`); the API shows it as stored (React escapes it).
- low · "N denials in all; see the audit log" is not a link with the filters applied; audit filters live in component state, not the URL.
- low · `Pager` shows "Showing 51–50 of 50" if the total shrinks while on a later page.
- low · `RequireSection` is cosmetic; the API's role matrix is the boundary (by design, UI-1).
- low · test gaps: no UI test for `resolved_llm == null` fallback text, empty states for eval runs/PII access, or second-page content.

## Left undone, and why
- No org model override in `resolved_llm`: no org override store exists yet (PRD §7.1); `resolve_llm` gets only the manifest. Wire it when the store exists (ADR 0014).
- The board page (`docs/plan/board.html`) was rebuilt locally by `plan.py build --board` but not republished: parallel branches each regenerate it; the orchestrator republishes after integrating.
- Playwright still uses UI-1's in-memory API, not Mongo-backed services (E2E-1's job); test-xt proves the Mongo path for the eval routes.

## Found for other goals
- **Orchestrator / every API goal:** `onb_api.main` now needs `LLM_CONNECTOR` and `LLM_MODEL_FAST/STANDARD/DEEP` at startup (it resolves `$CONFIG_DIR/llm.yaml`). Tests that import `onb_api.main` must set them (`test_telemetry.py`, `test_auth_settings.py` show how). Merge note: `create_app` gained `evals=` and `llm=` after TPL-2's `templates=`, `ensure_indexes`, `app.state` and router lists; `apps/web/src/App.tsx` gained `RequireSection` and an `/admin/*` route that other UI goals will likely mirror for their sections.
- **Other UI goals (UI-2..UI-7):** `apps/web/src/admin/parts.tsx` has generic `Loaded`, `Table`, `Pager`, `Badge`; consider lifting them (and `useAdminClient` → an app-wide `useApiClient`) into a shared module when merging. `adminApi.ts` `paged()` reads `meta.page` alongside `unwrap`.
- **REG-1 follow-up:** validate manifest aliases against `config/llm.yaml` at sync, so an unresolvable alias never reaches the registry.
- **E2E-1:** `apps/web/e2e/admin_seed.py` shows the admin data a golden path needs; through Caddy set `API_DEV_UPSTREAM`/`WEB_DEV_UPSTREAM` to the E2E ports.
- **MS-5:** U8 ledger row is `pass` with Playwright via Caddy.

## Decisions forced (ADRs written)
- `docs/adr/0014-admin-views-data-sources.md`: denials reuse `GET /audit`; the API resolves each agent's connector/model through `onb_llm.resolve_llm` (new `llm=` dependency, startup fail-fast); the eval routes' shape, paging and strictness. No PRD conflict built.

## Traps hit
- `libs/py-evals/tests/test_harness_boundary.py` forbids apps importing `onb_evals`, including test files and e2e seeds. The compatibility checks live in `tools/tests_xt` instead.
- `tools/tests_xt/test_xt_coverage.py::test_every_cross_tenant_test_runs_under_the_safety_net_guard` fails if you run xt tests in one pytest session together with `apps/api/tests`; run the suites separately (as `make` does).
- `test_role_matrix` needs a real sample record for path-parameter routes (HR_ADMIN 200), so it seeds one eval run.
- The worktree shell guard rejects long heredocs; write edit scripts to files.
- Rebase: TPL-2 wraps every xt check in `_reads_only(...)` (a check now also takes `unscoped`); new rows must use it.
