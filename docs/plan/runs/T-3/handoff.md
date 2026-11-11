# Handoff — T-3 · Prove no actor can reach another org        attempt 2 (rebased on main with REG-1, MCP-2, GR-1, T-2) · goal/t-3-cross-tenant-suite · see `git log -1`

**Outcome claimed.** `make test-xt` seeds two orgs (seed_v1's `org_acme` = A and `org_globex` = B) on
the compose Mongo and proves, for every API route, every mcp-mongo tool and the event-bus consumer
path, that an org-A actor cannot read, list, update or infer org-B data. A route, tool or consumer
with no case fails it, and `make check` runs it as its last required step.

**Attempt 2.** The orchestrator rebased the branch onto main, which brought REG-1's `GET /agents` and
`GET /agents/{name}` and MCP-2's validator. Two changes followed:
- **REG-1 routes:** both now have `ROUTE_CASES` rows (expected: HR_ADMIN 200, HR/MANAGER 403). The
  world syncs the global registry from `config/`, as REG-1's command does. Its audit records go to
  an in-memory log, so per-org audit totals are unchanged.
- **MCP-2 refusals:** MCP-2 now refuses any filter or pipeline that names `org_id` (including a
  `$org_id` reference). The MCP cases that had asserted such calls were answered with empty or org-A
  data now assert MCP-2's refusal instead: an error, `data is None`, the text
  `may not name org_id: tenant scope comes from the hub`, and none of org B's strings. That is
  stricter: nothing is read at all. No assertion was removed or loosened.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `make test-xt` passes and lists every current route | yes | `make test-xt` → `85 passed in 21.03s`, `make exit: 0`; the run ends by listing `cross-tenant surface: 6 API routes, 6 MCP tools, 0 event consumers` and each of `GET /agents`, `GET /agents/{name}`, `GET /audit`, `GET /health`, `GET /me`, `POST /auth/login` plus the six tools (`docs/evidence/T-3/make-test-xt.txt`). The route list is enumerated from `create_app()` (`tools/tests_xt/surface.py::api_routes`), not written by hand. |
| Adding a dummy route without a case makes `make test-xt` fail (seen once, output recorded) | yes | A `GET /xt-dummy` route added to `apps/api/src/onb_api/app.py`, then reverted: `FAILED tools/tests_xt/test_xt_coverage.py::test_every_api_route_has_a_cross_tenant_case` with `assert [('GET', '/xt-dummy')] == []`, `3 failed, 82 passed` (re-recorded on the rebased branch; the other two failures are coverage self-tests that compare exact route lists), `make exit: 2` (`docs/evidence/T-3/dummy-route-run.txt`). The same check runs in-suite as `test_a_route_added_without_a_case_is_uncovered`. |
| Counts and list totals never include the other org (test) | yes | `tools/tests_xt/test_xt_counts.py`: `test_the_audit_list_total_counts_one_org[org_acme/org_globex]` (`meta.page.total` and page sizes; B holds 5 audit records and A 3; `action=pii.reveal` exists in both orgs) and `test_mcp_counts_and_find_totals_count_one_org[<4 collections>-<both orgs>]` (`count`, `find.count` and `$count` aggregate). `test_both_orgs_have_data_in_the_counted_collections` keeps the counts non-vacuous. These run in the passing run above. Mutations M1 and M8 (org filter dropped from `count`) fail them. |
| `make check` or CI runs `make test-xt` as a required step | yes | `Makefile`: `check: plan-check env-check tooling-test lint typecheck test test-xt`. `make check` exit 0: `… 85 passed in 21.46s` then `make check: all passed` (`docs/evidence/T-3/make-check.txt`). The repo has no CI. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| A new API route fails `make test-xt` (and so `make check`) until `tools/tests_xt/test_xt_api.py::ROUTE_CASES` has a row: `RouteCase(request, check, expected={role: status}, volatile=…)`. Routes are keyed by their served path (router prefixes included). A websocket, mount or raw Starlette route is listed as `NOT-AN-API-ROUTE` and always fails until the suite learns it. | `test_every_api_route_has_a_cross_tenant_case`, `test_routes_are_listed_by_the_path_they_are_served_at`, `test_a_route_that_is_not_an_api_route_is_uncovered`, dummy-route run |
| Every route row automatically gets two generic checks for each org-A role: the response status equals `expected[role]` and the body and headers carry none of org B's seeded strings (`world.b_markers()`, which includes B's ids, names, emails, raw vault values and org name). Naming org B in query params (`org_id`, `orgId`, `org`, `tenant`, `tenant_id`) or headers (`X-Org-Id`, `X-Tenant-Id`, `X-Org`) changes nothing; naming it in a JSON body is ignored or refused with a 422. | `test_no_org_b_data_reaches_an_org_a_actor[*]`, `test_naming_org_b_in_the_request_changes_nothing[*]`, M2 |
| A new tool on a server in `surface.MCP_SERVERS` fails the suite until `test_xt_mcp.py::TOOL_CASES` has a row. A module that builds a new `FastMCP(...)` fails `test_every_mcp_server_in_the_code_is_enumerated` until it is listed in `MCP_SERVER_MODULES` and `MCP_SERVERS`. Each tool row gets: no B strings in the ordinary result, a top-level `org_id: org_globex` argument refused, and no org-B document changed in any collection. | `test_every_mcp_tool_has_a_cross_tenant_case`, M6, `test_an_org_id_argument_naming_org_b_is_refused[*]` |
| Event consumers: put each production subscription map in `surface.PLATFORM_SUBSCRIPTIONS`, and give each consumer a `test_xt_consumers.py::CONSUMER_CASES` row (`ConsumerCase(event_type, payload, handler)`). A module that builds a `MongoEventBus` fails `test_every_event_bus_in_the_code_is_enumerated` until it is listed in `BUS_MODULES`. The generic check runs the real handler: an org-A job leaves every org-B document unchanged, org B's same idempotency key is not a duplicate, and interleaved A/B jobs on one worker each run in their own org. Until RUN-1 adds a consumer it runs on the suite's `xt.probe` consumer. | `test_a_consumer_handling_an_org_a_event_stays_in_org_a[xt.probe]`, M5, M10 |
| REG-1's `/agents` and `/agents/{name}` are global: an org-A admin and an org-B admin get identical answers, no record names an org, and an org-B name (`globex_doc_agent`) answers 404 exactly like a name nobody has, never 403. | `test_the_routes_own_cross_tenant_checks[GET /agents*]`, M11, M12 |
| MCP: a filter, pipeline or `$org_id` reference naming an org is refused with MCP-2's reason and no data (`_assert_refused_for_naming_org`). | `test_the_tools_own_cross_tenant_checks[mcp-mongo.find/count/aggregate]`, M13 |
| Inference: an org-B id or value answers exactly like one that does not exist (`world.assert_indistinguishable`) on `/me` (B user id in an org-A token → same 401), `/audit` filters, login (B email + wrong password → same 401 as unknown email), MCP find/count/aggregate/update by B's ids, and `insert_one` with B's `_id`. | `test_the_routes_own_cross_tenant_checks[*]`, `test_the_tools_own_cross_tenant_checks[*]`, M3, M7 |
| The suite runs under the T-2 safety net. `tools/tests/test_safety_net_suites.py` now also checks root suites (`ROOT_SUITES = ("tools/tests_xt",)`), so `make check` fails if the plugin import or the guard test is removed. | `test_every_mongo_integration_suite_runs_under_the_safety_net`, `test_a_root_suite_without_the_plugin_is_reported`, `test_every_cross_tenant_test_runs_under_the_safety_net_guard` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-TENANT @ MS-6 | compose, 2026-11-11 | contributes: suite passes 100% for 6 routes, 6 MCP tools, consumer path | `docs/evidence/T-3/make-test-xt.txt` | no (MS-6 is the last prover) |
| SM-3 @ MS-6 | compose, 2026-11-11 | contributes: no cross-tenant access through any current route, tool or consumer path | `docs/evidence/T-3/make-test-xt.txt`, `mutations.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `85 passed in 21.46s` (test-xt step) · `make check: all passed`, exit 0 (`docs/evidence/T-3/make-check.txt`) |
| Cross-tenant | `make test-xt` | `85 passed in 21.03s`, exit 0 (`docs/evidence/T-3/make-test-xt.txt`) |
| Integration | `make test-int` | exit 0 · `NX Successfully ran target test-int for 7 projects` (`docs/evidence/T-3/make-test-int.txt`), run on the rebased branch at the orchestrator's request |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date`, exit 0 |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| AUTH-1 | Routes run inside `tenant_scope(principal.org_id)`; `NotFound` → 404; `onb_api.testing` token helpers; `MongoUserStore.add_user` inside `tenant_scope` | yes: `/me` and `/audit` cases; M2 and M7 kill |
| AUTH-1 | Unknown email still costs one bcrypt check | the body and status match (checked); timing not measured (see open findings) |
| T-2 | `onb_data.safety_net_pytest` five names + a guard fixture test; `unscoped("reason")` for root reads | yes: every test of the suite is guarded |
| AUD-1 | `GET /audit` HR_ADMIN only; `meta.page.total`; org from the token; org query params ignored | yes: `/audit` cases and the counts test |
| EVT-1 | `bus.consumers()`; `bus.worker({...}).run_once()`; handlers run inside the job's tenant scope; idempotency keys are per org | yes: consumer check; M5 and M10 kill |
| MCP-1 | Call-context contract, `org_id` rejection, `$oid` handling, `_id` refusal on insert | yes: tool cases |

## Mutation checks
Script: `docs/evidence/T-3/mutate.py`. Full output: `docs/evidence/T-3/mutations.txt`. Each mutation was applied, the suite was run, the change was restored, and the working tree was confirmed clean.
- **M1** `TenantRepository.count` drops the org filter → `test_mcp_counts_and_find_totals_count_one_org[*]`, `test_the_tools_own_cross_tenant_checks[mcp-mongo.count]`, probe consumer: `10 failed, 61 passed, 11 errors` (KILLED).
- **M2** an `X-Org-Id` header picks the API tenant scope → `test_naming_org_b_in_the_request_changes_nothing[GET /me-*]`, `[GET /audit-HR_ADMIN]`: `4 failed` (KILLED).
- **M3** login answers 403 for a known email with a wrong password → `test_the_routes_own_cross_tenant_checks[POST /auth/login]`: `1 failed` (KILLED).
- **M4** mcp-mongo `find` runs in org B's scope → `test_the_tools_own_cross_tenant_checks[mcp-mongo.find]`, `test_no_org_b_data_reaches_an_org_a_agent[mcp-mongo.find]`, A-side counts: `6 failed` (KILLED).
- **M5** the worker runs a handler in another org → `test_a_consumer_handling_an_org_a_event_stays_in_org_a[xt.probe]`: `1 failed` (KILLED).
- **M6** the `mcp-mongo.count` case is deleted → `test_every_mcp_tool_has_a_cross_tenant_case`: `1 failed` (KILLED).
- **M7** `/me` looks the user up across orgs → `test_the_routes_own_cross_tenant_checks[GET /me]`: `1 failed` (KILLED).
- **M8** `AppendOnlyRepository.count` (the audit total) drops the org filter → `test_the_audit_list_total_counts_one_org[*]`, `[GET /audit]`: `3 failed, 5 errors` (KILLED).
- **M9** the enumerator keys routes by `original_route.path` instead of the served path → `71 passed`. **SURVIVED, equivalent mutant on FastAPI 0.162.2:** `include_router` copies routes with the prefix already applied, so both paths are `/hires/{item_id}` (checked with a probe script). `context.path` is kept because it is the served path by contract. `test_routes_are_listed_by_the_path_they_are_served_at` would catch a FastAPI change that makes them differ.
- **M10** the worker keeps the first job's tenant for later jobs → the interleaved phase of the consumer check: `1 failed` (KILLED).
- **M11** `GET /agents/{name}` answers 403 instead of 404 for an unknown name → `test_the_routes_own_cross_tenant_checks[GET /agents/{name}]`: `1 failed` (KILLED).
- **M12** `GET /agents` answers differently by the caller's org → `test_the_routes_own_cross_tenant_checks[GET /agents]`: `1 failed` (KILLED).
- **M13** MCP-2's validator stops refusing `org_id` field paths → `test_the_tools_own_cross_tenant_checks[mcp-mongo.find/count/aggregate]`: `3 failed` (KILLED). In attempt 1 these `$org_id` calls were allowed and stayed inside org A; MCP-2 now refuses them, and the suite holds it to that.
- Attempt 2 re-ran every mutation on the rebased branch: 12 of 13 killed; M9 is still equivalent (`85 passed`).
- Box 2 dummy route (above): KILLED by the coverage check.

## Review findings left open
- LOW · `tools/tests_xt/surface.py::modules_calling` · The constructor scan finds only direct calls named `MongoEventBus`/`FastMCP`. An import alias or a factory function would get past it. Left because there is no production bus or second server yet; RUN-1/HUB-1 should keep constructor names plain or extend the scan.
- LOW · `tools/tests_xt/test_xt_api.py::_login_check` · Login timing is not measured. A known email costs two lookups plus a real bcrypt check; an unknown one costs one lookup plus the stand-in bcrypt. A timing test would be flaky on shared hardware; measuring it belongs with a perf/hardening goal.
- LOW · `tools/tests_xt/world.py` · Markers are strings of 6+ characters unique to org B. Numbers and transformed values (hashes, case changes) are not detected. `VOLATILE_KEYS` (`at`, `trace_id`, …) are dropped at every depth before foreign-vs-missing comparisons.
- LOW · `tools/tests_xt/conftest.py` · The `api` and `mcp_mongo` stores are not closed explicitly (their event loops are gone by teardown). `_free_port` can race another process for the port.
- LOW · The steering check covers fixed parameter and header names, not path segments or cookies. No current route takes an org-like path segment.
- (Closed by MCP-2.) `$org_id` field references are now refused by MCP-2's validator. The suite asserts the refusal (M13).

## Left undone, and why
- No production event consumer exists (`PLATFORM_SUBSCRIPTIONS` is empty, `BUS_MODULES` is empty). The extension point and a working generic check are in place; RUN-1 fills them.
- `apps/api/tests_int/conftest.py` still copies the Mongo tunnel fixture; the xt suite has a fourth copy with its own label. Sharing one test-support module touches four projects and was left out of scope (see below).

## Found for other goals
- **Future routes (HIRE-1, TPL-2, …):** every route fails `make test-xt` until it has a `ROUTE_CASES` row with `expected` per role. If `create_app` gains a store argument, pass a Mongo-backed one in `tools/tests_xt/conftest.py::build_api`, as now done for REG-1's `agents`. Id routes must use `assert_indistinguishable` (foreign id == missing id, 404).
- **New mcp-mongo tools:** they are enumerated automatically and need `TOOL_CASES` rows.
- **HUB-1 / mcp-mocks:** a new `FastMCP(...)` module must be added to `surface.MCP_SERVER_MODULES` and `MCP_SERVERS`, with a case per tool.
- **RUN-1 (first consumers):** add the subscription map to `surface.PLATFORM_SUBSCRIPTIONS`, the module to `BUS_MODULES`, and a `CONSUMER_CASES` row per consumer.
- **Decision for Haneef (user creation, e.g. a user-admin route):** ADR 0004 makes emails unique across orgs (`user_logins._id = sha256(email)`). Any route that creates users will let an org-A admin learn that an email is registered in org B (`EmailTakenError`). That contradicts §7.9's "infer" rule, and the suite's create-route rule cannot pass under it. Decide before such a route exists, e.g. a generic "could not create user" answer or an invite flow.
- **Any future route accepting client-chosen `_id`s:** the `_id` index is collection-wide, so a duplicate-key error would reveal org B's ids. Generate ids server-side, as mcp-mongo does.
- **Ops/test-support:** a shared Mongo tunnel fixture would remove four copies (py-data, api, py-eventbus, tools/tests_xt).
- **Make check now needs Docker** (it runs `up` through `test-xt`). Sessions running `make check` need a `.env` with their own `COMPOSE_PROJECT_NAME`.

## Decisions forced (ADRs written)
None. The suite's location (`tools/tests_xt`, a root suite) follows `tools/tests_int`.

## Traps hit
- `AuditLog` counts through `AppendOnlyRepository.count`, not `TenantRepository.count`, so a mutation of the latter leaves `/audit` totals intact (M8 covers it).
- The `rtk` hook truncates long `make` output written to a file; evidence runs use `rtk proxy make`.
- Starlette's `TestClient` returns `httpx2` responses, so the suite types them with a small protocol.
- The org-B snapshot first compared empty collections created during the test; it now compares only collections that hold org-B documents.
