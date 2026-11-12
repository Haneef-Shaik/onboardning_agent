# Handoff — UI-5 · Run board with live status, blocked reasons and trace links        attempt 1 · goal/ui-5-run-board · see `git log -1` (handoff commit)

**Outcome claimed.** HR/HR_ADMIN open `/runs`, see every run's status, open one and watch its tasks change live over `GET /runs/{id}/stream`, see which task is blocked and why, retry it or complete it by hand with a reason, pause/resume/cancel the run, and open the run's trace in Jaeger at `<jaeger>/trace/<trace id>`. A manager sees their reports' runs and the blocked reasons but gets no controls and no trace link.

Code: `apps/web/src/runs/` (`sse.ts` fetch-body SSE reader, `useRunStream.ts`, `RunsPage`, `RunDetail`, `StatusBadge`, `policy.ts`, `runs-api.ts`), mounted in `App.tsx` as `/runs/*` behind `RequireSection`. API (the one addition the card needed): `RunRecord.trace_id` (32 hex, from the run's stored `traceparent`; `None` when absent or malformed), `apps/api/src/onb_api/runs/records.py` + `libs/py-contracts/.../runs.py`; contracts and TS client regenerated. Test seed: `apps/web/e2e/run_seed.py` (real `RunService` over an in-memory store, template published, and a test-only `/e2e/runs` router that plays the orchestrator with the same `RunStore.move_task/move_run` calls); `serve_api.py` mounts it. No ADR needed (0022 unused).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Playwright: run detail updates live (SSE) as tasks change | yes | `apps/web/e2e/runs.spec.ts › run detail updates live over SSE as tasks change`: opens a run, `stream-state` = Live, then the test router moves tasks (`running`, `done`, next task `running`) and the page shows each status and the activity line `<task>: ready → running` with **zero navigations** (asserted via `framenavigated`). `E2E_API_PORT=19810 E2E_WEB_PORT=19811 npx playwright test --config apps/web/playwright.config.ts` → `17 passed` (`docs/evidence/UI-5/playwright.txt`); through Caddy (`ob-ui-5`, `E2E_BASE_URL=https://localhost:19801`) → `17 passed` (`playwright-via-caddy.txt`). Unit: `useRunStream.test.tsx` (5: events, timeout reconnect, drop retry, 404 stop, unmount), `sse.test.ts` (4). |
| Playwright: a blocked task shows its reason, and retry / complete-with-reason work | yes | `runs.spec.ts › a blocked task shows its reason; retry puts it back in play` (run `blocked`, failed task shows `sink_rejected` as "rejected…", sibling shows none; Retry → task leaves `failed`, reason gone) and `› a blocked task is completed by hand only with a reason` (Confirm disabled until a reason is typed; then task `done`). Unit: `RunDetail.test.tsx` (retry call, complete body `{reason}`, blank reason refused, controls per state), `policy.test.ts` (11). Real API behind the spec: ADR 0018 routes. |
| The trace link opens Jaeger on the run's trace id (Playwright asserts the URL) | yes | `runs.spec.ts › the trace link opens Jaeger on the run's trace id`: the link's `href` equals `https://jaeger.localhost/trace/<trace id of that run>`, the click opens a new tab whose `url()` is exactly that (Jaeger itself is stubbed at the network layer: it sits behind Caddy basic auth and is not part of the e2e server). The id comes from the run's stored traceparent through the API. API: `test_run_trace_id.py` (9). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `GET /runs/{id}` returns `trace_id` (32 lowercase hex or null); lists do not. | `test_run_trace_id.py`; `make contracts-check` up to date |
| `/runs`, `/runs/:runId` exist; the run page is the place for UI-7's chat panel (`RunDetail` takes `client`, `role`, `runId`, `jaegerBase`). | `runs.spec.ts`, `RunDetail.test.tsx` |
| Web: `useRunStream(client, runId, {onChange})` follows the stream with the bearer token (fetch, not EventSource), reconnects at once-ish (250 ms) on `end {reason:"timeout"}`, backs off on drops, stops on 401/403/404 or a finished run; each event asks the screen to re-read the run, so the API is the truth. | `useRunStream.test.tsx`; mutants M7–M9 |
| Jaeger address: `VITE_JAEGER_URL`, default `https://jaeger.localhost` (the compose proxy name, no port). | `RunsPage.tsx` `JAEGER_BASE` |
| e2e: `POST /e2e/runs` starts a run for the seeded hire; `POST /e2e/runs/{id}/move {task_id,to,error?}` plays the orchestrator (to `ready|running|done|failed`; a failure blocks the run). Specs use the seeded hire so UI-8's audit counts (one `hire.create`) hold in parallel. | `run_seed.py`, `runs.spec.ts` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U4 @ MS-5 | compose (`ob-ui-5`, Caddy 19801), 2026-11-12 | pass | `docs/evidence/UI-5/playwright-via-caddy.txt` | yes — UI-5 is the last prover |
| U7 @ MS-6 | — | contributes evidence only (trace link on the run page; trace *content* is F-4/RUN-3/DEMO-1) | `runs.spec.ts` trace test | no |
| NF-OBS @ MS-6 | — | contributes evidence only (every run has a trace link) | same | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | every step before test-xt passed (plan-check, env-check, tooling-test 179 passed, lint, typecheck, unit tests for 17 projects, coverage); test-xt step: `309 passed, 7 errors`, all Mongo `connection closed` / `No keys found for HMAC` at fixture setup (load average 14 from parallel sessions), `make-check.txt` |
| Cross-tenant | `uv run --frozen --env-file .env pytest tools/tests_xt` (what test-xt runs) | `316 passed in 670.17s` (`test-xt-rerun.txt`); `cross-tenant surface: 36 API routes`. Three earlier `make` runs each had only the known Mongo setup errors in different tests (1, 2 and 7), none the same twice; none touches this diff |
| Contracts | `make contracts-check` | `openapi.json and the TS client are up to date` |
| Playwright | direct and via Caddy | `17 passed` both |
| Web unit | `vitest run --config apps/web/vitest.config.ts` | `125 passed (125)`, 14 files |
| Eval self-test / integration | not run | no agent, prompt, guardrail, store or consumer touched |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-2 | routes, SSE `task`/`run`/`end` events, `end.reason=timeout` means reconnect, manager scoping, `complete` needs a reason | yes (`runs.spec.ts` drives them for real) |
| UI-1 | `RequireSection`/nav table, token in memory, API client middleware | yes; the stream goes through the same client (`parseAs: "stream"`), so the bearer and 401 handling apply |
| UI-8/UI-4 | e2e seed (`admin_seed.py`) | yes; extended additively |
| F-4 | `traceparent` stored on the run | yes; its trace id is exposed |

## Mutation checks
`uv run --frozen python docs/evidence/UI-5/mutate.py --write` → `mutation-checks.txt`, `SUMMARY: 13 of 13 mutations caught`: M1 stream never re-reads → `updates live` spec; M2 reason hidden → `blocked task shows`; M3 complete without reason → `completed by hand`; M4 trace link wrong id → `trace link`; M5/M6 managers get controls / trace → `manager sees`; M7 timeout end stops, M8 drop not retried, M9 log not reset → `useRunStream.test.tsx`; M10 trace id from whole traceparent, M11 all-zero id accepted → `test_run_trace_id.py`; M12 link for malformed id, M13 blocked run offers pause → `policy.test.ts`. Files restored by `git checkout` (the script needs a committed tree).

## Review findings left open
A reviewer subagent (code + security, on `git diff main...HEAD`) found no critical or high issue.
- medium · `control.py` `get` / `records.py` · a MANAGER in scope receives `trace_id` from `GET /runs/{id}`; only the UI withholds the link. A trace id is a random id, no PII, and Jaeger is behind basic auth, but if "no trace for managers" is a data rule, blank it server-side for MANAGER (small change in RUN-2's module; left for SEC-2/Haneef).
- low (fixed) · reconnect after `end timeout` had no pause (now 250 ms); abort listener in `sleep` was never removed (now `once` + removed).
- low · the activity log is cleared at each reconnect before the replay arrives; a brief "Nothing yet." flash. By design (the server replays history).

## Left undone, and why
- Run list shows `hire_id`, not the hire's name (managers cannot list hires, and a join would be N calls); a name column wants a hire name in `RunSummary`.
- No status filter control in the list (API supports `?status=`; `listRuns` takes it).
- Playwright asserts the Jaeger URL only; opening the real Jaeger (basic auth) is E2E-1's.

## Found for other goals
- **UI-1's `smoke.spec.ts`** asserted no API call after sign-in other than login and `/me`, which held while Runs was a placeholder. Runs is the landing page and now lists runs, so the assertion became `expect.poll(...).toEqual([login, me, GET /api/runs])` (one line, this card's own consequence).
- **UI-7:** mount the chat panel in `RunDetail`; `Open trace` is in the header.
- **E2E-1:** set `VITE_JAEGER_URL` to carry the proxy's port (`https://jaeger.localhost:<CADDY_HTTPS_PORT>`) when the browser reaches Caddy on a non-default port; the default has no port.
- The board (`docs/plan/board.html`) was not rebuilt or republished here (U4's row changed): left to the orchestrator to avoid clobbering parallel sessions.
- Flake: `make test-xt` Mongo `connection closed` at setup under load (known).

## Decisions forced (ADRs written)
None. Choice made without an ADR: HR and HR_ADMIN both get the trace link (the card says HR; PRD U7 names HR_ADMIN); managers do not.

## Traps hit
- `mutate.py` restores files with `git checkout`, which silently discarded uncommitted edits; re-applied and committed first.
- A new `hire.create` audit row per spec raced UI-8's exact-count assertion on the shared e2e server (only visible through Caddy's slower timing); fixed by starting runs on the seeded hire.
- The template's `role` variable is an enum (`engineer|designer|sales`); the UI-4 form's free-text role ("Engineer") does not compile into a run.
