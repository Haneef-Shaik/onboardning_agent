# Handoff — UI-6 · Approvals inbox and mock Telegram outbox        attempt 1 · goal/ui-6-approvals-ui · HEAD: see `git log -1`

**Outcome claimed.** Approvers decide from the web Approvals Inbox or from the mock outbox's Approve/Reject buttons;
a button press goes to a new authenticated route that calls `POST /webhooks/telegram` in-process with the secret
(the same endpoint a real Telegram callback hits), and either decision resumes the run; a second decision shows
"already decided". Design: `docs/adr/0026-mock-outbox-press-relays-to-the-telegram-webhook.md`.

**Entry gate.** UI-1 and NOTIF-1 are `done`. HIL-1 is `on hold` on the board only for its real-connector eval box
(needs OW-1); its code is on main (5f61a29) and the orchestrator's board note says UI-6 builds on `GET /approvals`
and `POST /approvals/{id}/decision`. Started on that basis (Haneef's orchestration note).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Playwright: approving from the inbox resumes the run | yes | `apps/web/e2e/approvals.spec.ts` › "approving from the inbox resumes the run": run started by the real `orchestrator.start_run` waits at `manager_approval` (`waiting_approval`, `laptop` pending); Ravi approves in the inbox (packet shown first); the run page then shows `manager_approval` done, `laptop` done, run `completed` (resumed by the real `approval.decided` → `orchestrator.resume_approval`). Command `E2E_API_PORT=19510 E2E_WEB_PORT=19511 node_modules/.bin/playwright test --config apps/web/playwright.config.ts` → `23 passed` (`docs/evidence/UI-6/playwright-e2e.txt`). |
| Playwright: the mock outbox's Approve button calls `POST /webhooks/telegram` and resumes the run | yes | same file › "the mock outbox's Approve button calls POST /webhooks/telegram and resumes the run": the browser's request is `POST /api/mock-outbox/{id}/press` (asserted) and never `/webhooks/*` (asserted); server-side, the webhook route's request counter rose and the webhook handler recorded `decision.received` for **this** message id (`/e2e/webhook-decisions`); the inbox then shows the decision `via telegram` (set only by `orchestrator.record_decision`, i.e. the webhook path) and the run is `completed`. API-level proof that the route runs through the webhook route: `apps/api/tests/test_outbox_press.py::test_pressing_approve_on_my_message_calls_the_telegram_webhook_and_publishes_the_decision` (records `("POST", "/webhooks/telegram")` in the app's middleware). |
| Playwright: a second decision shows "already decided" | yes | same file › 'a second decision shows "already decided"': another session rejects first; the stale inbox's Approve then shows the alert "Already decided…" (API 409), and the outbox's Approve shows it too (press route 409); the first decision stands (task and run `failed`, ADR 0020). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `POST /mock-outbox/{message_id}/press {callback_data}`: 200 `{ok: true}` after calling `POST /webhooks/telegram` in-process; 404 unless the message (token's org) is addressed to the caller and the button is on it; 409 when its approval is not `pending`; 503 when the webhook call fails; 422 for a malformed button id. One callback query per (message, button): repeats publish once. | `apps/api/tests/test_outbox_press.py` (26), role matrix row, xt row `POST /mock-outbox/{message_id}/press` |
| `GET /mock-outbox` messages carry `audience_user_id`. | `test_outbox_press.py::test_the_outbox_says_who_each_message_is_addressed_to`, `test_notify_routes.py` |
| `onb_api.notify.relay.post_json(app, path, payload, headers=…)`: one in-process ASGI POST through the whole app in an empty context; non-2xx or a raise → `RelayError`. | `test_outbox_press.py` relay tests |
| Web: `/approvals` (inbox: the caller's approvals, `mine=true`) and `/approvals/outbox` (mock outbox; buttons only on the caller's messages). | `apps/web/src/approvals/ApprovalsSection.test.tsx` (12) |
| E2E: `apps/web/e2e/approval_seed.py` runs approval flows through the real orchestrator consumers and `notify.approval` over the e2e memory store (`POST /e2e/approval-runs`). | the 3 specs above |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U5 @ MS-6 | Playwright (in-memory API + real orchestrator consumers), 2026-11-13 | contributes: approval request with AI-prepared (stub) summary, one-action approve/reject from inbox and from the Telegram mock | `docs/evidence/UI-6/playwright-e2e.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (stack `ob-ui-6`, ports 19500/19501) | `make check: all passed`, `make check exit 0` (`docs/evidence/UI-6/make-check.txt`; xt: `cross-tenant surface: 42 API routes, 9 MCP tools, 8 event consumers`, row `POST /mock-outbox/{message_id}/press`). First attempt hit the known Mongo flake at test-xt setup (`AutoReconnect … connection closed` at setup of `test_naming_org_b_in_the_request_changes_nothing[GET /agents/{name}-HR]`, 360 passed, 1 error; `make-check-attempt1-flake.txt`); re-run clean. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (`contracts-check.txt`) |
| Web e2e | `E2E_API_PORT=19510 E2E_WEB_PORT=19511 node_modules/.bin/playwright test --config apps/web/playwright.config.ts` | `23 passed` (`docs/evidence/UI-6/playwright-e2e.txt`) |
| Web unit | `vitest run --config apps/web/vitest.config.ts` | `Test Files 16 passed (16)`, `Tests 146 passed (146)` |
| API unit | `uv run --frozen pytest apps/api/tests` | 878 passed (role matrix and notify routes included) |
| Integration | `make test-int` | `make test-int exit 0` (`docs/evidence/UI-6/make-test-int.txt`: 12/12/5/50/11/79/19/38/45/4/26 passed). Attempt 1: Mongo `ServerSelectionTimeout`/`connection closed` under memory pressure from parallel stacks, no assertion failure (`make-test-int-attempt1-mongo-timeouts.txt`); attempt 2: only the eval self-test's cleanup check found `onb_eval_61e4d3c5380c29eb` left by attempt 1's crash (self-test itself `gates: PASS`; `make-test-int-attempt2-eval-leftover.txt`); dropped that database in the `ob-ui-6` Mongo; attempt 3 clean. |
| Evals | — | not touched (no agent, prompt or dataset change) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| HIL-1 (ADR 0020) | `GET /approvals?mine=`, `POST /approvals/{id}/decision` (first wins, 409), `approval.decided` resumes; `record_decision` attributes a press to the message's `audience_user_id` | yes, exercised end to end in Playwright with the real consumers |
| NOTIF-1 (ADR 0013) | `GET /mock-outbox`, `POST /webhooks/telegram` (secret, `decision.received` keyed by `callback_query.id`), `MockChannels`, `DecisionPublisher` protocol | yes; the press reuses the webhook unchanged |
| UI-5/UI-7 | RunDetail task test ids and status badges; run_seed's in-memory run store | yes (run_seed got one additive `data=` field) |
| UI-8 | `RequireSection`, nav table (Approvals already for all roles) | yes |

## Mutation checks
`docs/evidence/UI-6/mutate.py` → `docs/evidence/UI-6/mutation-checks.txt`: **17/17 killed**, `source diff after all mutations: 0 files differ`.
- M1 audience check removed → `test_a_message_addressed_to_someone_else_is_404_and_publishes_nothing[*]`, `test_a_message_without_an_audience_is_404`
- M2 button-on-message check removed → `test_a_button_that_is_not_on_that_message_is_404`
- M3 409 pre-check removed → `test_a_press_on_an_approval_already_decided_is_409_already_decided[*]`
- M4 handler called directly, webhook route skipped → `test_pressing_approve_on_my_message_calls_the_telegram_webhook…`
- M5 relay inherits the caller's context → `test_the_relayed_request_does_not_inherit_the_callers_tenant_scope`
- M6 non-2xx relay ignored → `test_a_relay_that_answers_an_error_raises`, `test_a_webhook_that_refuses_the_relay_is_503…`
- M7 secret returned to the browser → press tests (and `test_the_browser_never_receives_the_webhook_secret`)
- M8 random query id per press → `test_pressing_the_same_button_again_publishes_once`
- M9 relay lets a route's exception escape → `test_a_relayed_route_that_raises_is_a_relay_error`
- W1–W5 (UI: buttons only for the audience, 409 → "already decided" in inbox and outbox, the pressed button's id, `mine=true`) → the named vitest cases
- E1 (webhook skipped) → Playwright box 2; E2 (web decision dropped) → boxes 1 and 3; E3 (409 pre-check removed) → box 3

## Review findings left open
Two independent reviews (code, security) of `git diff main...HEAD`: no CRITICAL or HIGH. Fixed: security MEDIUM
(unbounded presses → one callback query per message and button, M8), relay exceptions → 503 (M9), non-numeric stored
`message_id`, shared presser dict, misleading success text, weak e2e webhook assertion (now per message id), unclosed
browser context. Left open:
- **LOW · `service.py` `press_update` / `MockOutbox.tsx`**: the 409 is an early check only; a decision that lands
  between it and the relay gives the press a 200 although it decides nothing (first decision still wins in
  `ApprovalDecider`). The UI text says "sent", and the inbox shows the outcome. ADR 0026 §1.
- **LOW · `relay.py`**: the relayed request runs in an empty context, so its span is not linked to the press
  request's trace (deliberate, ADR 0026); a span link could be added in OBS work.
- **LOW · route has no per-user rate limit** (no route in the API has one yet: SEC-2). Repeats of one button are now
  deduplicated, so a message publishes at most one event per button.
- **INFO · `GET /mock-outbox` shows `audience_user_id`** (an internal user id, not PII) to HR/HR_ADMIN, who already see
  the whole org's outbox.

## Left undone, and why
- No compose-stack Playwright run (Caddy + real Mongo bus + orchestrator service): the e2e API is the in-memory one
  (UI-1's design); there is no orchestrator compose service yet (DEP-1). The approval flow's Mongo path is HIL-1's
  integration tests; this goal's e2e uses the real consumers over the in-memory store with a pump in place of the
  Mongo bus.
- `/goal` could not be set from this orchestrated session; I worked to the text `plan.py goal UI-6` printed.

## Found for other goals
- **SEC-2**: per-user rate limit on `POST /mock-outbox/{id}/press` (and the API generally).
- **E2E-1 / DEP-1**: the golden path on compose should press the outbox button through Caddy once the orchestrator
  and mcp-notify services exist.
- **HIL-2**: the inbox lists `sla_hours`/`requested_at` data already; overdue display is not built here.

## Decisions forced (ADRs written)
- `docs/adr/0026-mock-outbox-press-relays-to-the-telegram-webhook.md` (the reserved number).
- Shared-file edits, all additive: `apps/web/src/App.tsx` (one route), `apps/web/e2e/serve_api.py` (approval world,
  two test routers, one middleware), `apps/web/e2e/run_seed.py` (`RunDriver.data`), `apps/api/tests/test_role_matrix.py`
  (one row, one sample), `tools/tests_xt/test_xt_api.py` (one route case), `apps/api/tests/test_notify_routes.py`
  (the exact field set gains `audience_user_id`), contracts regenerated (`openapi.json`, `schema.ts`).

## Traps hit
- The runner reads the approver's `manager_id` from `new_hires` in the runs' database; the e2e hire service keeps
  its own store, so the harness mirrors `{_id, manager_id}` there (otherwise the approver falls back to the HR role).
- `apps/web/e2e/*.py` and `docs/evidence/**` are outside the ruff gate (main's `run_seed.py` already has a warning).
- `rtk` filters `make` output; run gates as `rtk proxy make …` to keep the result lines.
