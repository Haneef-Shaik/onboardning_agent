# Handoff — UI-7 · Chat with the orchestrator on the run page        attempt 1 · goal/ui-7-run-chat-ui · see `git log -1`

**Outcome claimed.** The run page has a chat panel: HR, HR_ADMIN and managers (their reports' runs) ask about the run and get an answer whose cited tasks are links to those tasks' rows; a proposed retry changes nothing until HR presses "Confirm retry" in a dialog, which calls `POST /runs/{id}/chat/confirm` once with the proposal shown.

Code: `apps/web/src/runs/chat/` (`chat-api.ts`, `ChatPanel.tsx`, `MessageView.tsx`, `ConfirmRetryDialog.tsx`), mounted in `RunDetail.tsx` (task rows gain `id="task-<id>"` + `tabIndex=-1` so a citation can focus them). `libs/ts-api-client` exports `ChatMessageRecord`, `ChatCitation`, `ChatProposal` (types only; schema unchanged). e2e: `apps/web/e2e/chat_seed.py` (the real `ChatService` over the in-memory store with a scripted `RunAssistant`, so **no model is called**), `run_seed.py` (+3 lines: builds it, `RunDriver.chat`), `serve_api.py` (+`chat=driver.chat`), `chat.spec.ts`. No API code changed. No ADR (0025 unused).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Playwright: a question gets an answer whose citations link to tasks | yes (stub connector) | `chat.spec.ts › a question gets an answer whose citations link to tasks`: HR asks on a run with a failed task; the answer cites that task, exactly one link, `href="#task-<id>"`; click focuses that task's row (and not another's); the run stays blocked. `E2E_API_PORT=19410 E2E_WEB_PORT=19411 npx playwright test --config apps/web/playwright.config.ts` → `PASS (20) FAIL (0)` (`docs/evidence/UI-7/playwright.txt`). Unit: `ChatPanel.test.tsx` (9 + 2). |
| Playwright: a proposed action shows a confirm dialog and runs only on confirm | yes (stub connector) | `chat.spec.ts › a proposed action shows a confirm dialog and runs only on confirm`: proposal on screen + typing "yes, do it" → no write request, run still blocked; Review → dialog "Retry task <id>?"; Cancel → no write; Review → Confirm retry → exactly one `POST /api/runs/<id>/chat/confirm` (and no page-retry route), task leaves `failed`, run leaves `blocked`, "Retry confirmed", button gone. Also `› a manager can ask ... never offered the retry`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| The run page (`RunDetail`) shows an assistant panel to every role that can open the run; only HR/HR_ADMIN can open the confirm dialog. | `ChatPanel.test.tsx › gives a manager the answer but never a way to confirm`; `chat.spec.ts` manager test; M3 |
| The confirm call sends the proposal id of the message reviewed, only from the dialog's Confirm button; Cancel/Escape send nothing. | `ChatPanel.test.tsx` (3 dialog tests + 2-proposal test); M1, M2, M5; Playwright M1 |
| `serve_api.py` serves the chat routes for e2e (`create_app(chat=...)`); a message containing "retry" gets a proposal for the run's first failed task. | `chat.spec.ts`, `chat_seed.py` |
| E2E-1/DEMO-1 can use real chat only with `AGENT_TOKEN_KEY` + `MCP_HUB_URL` set (else 503, panel shows the error). | CHAT-1 ADR 0024 §5 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U6 @ MS-5 | dev servers (not Caddy), 2026-11-13 | UI half proved with a scripted assistant | `docs/evidence/UI-7/playwright.txt` | **no, left `open`**: U6 says "a grounded answer"; that needs the real assistant (CHAT-1's real-connector eval, OW-1) which UI-7 cannot show without a paid call. Orchestrator: set U6 to pass after CHAT-1's eval, citing this evidence for the UI half. |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (raw output `make-check.txt`, taken before the final review fixes) | exit 0, `make check: all passed`; web unit `132 passed`, test-xt `337 passed in 453s`, no Mongo flake this run. After the review fixes (web-only): `make lint` clean, `tsc` ok, web unit `134 passed (134)` |
| Playwright | direct, final code | `PASS (20) FAIL (0)` |
| Contracts | `make contracts-check` | `openapi.json and the TS client are up to date` |
| Integration / evals self-test | not run | no API, agent, prompt or guardrail code touched |
| Caddy run | not run | UI-5 proved the Caddy path; this adds one panel on the same page; `make down` run |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| CHAT-1 | `POST /runs/{id}/chat` SSE `message`+`end`; `GET` history; `POST .../chat/confirm` HR only; expired proposal reads `failed`; citations only for the run's tasks | yes (real service in e2e) |
| UI-5 | `RunDetail` takes `client, role, runId`; run re-read on invalidate | yes; confirm invalidates `["run", id]` |
| CHAT-1 status | on hold for its real-connector eval only | code on main used; nothing here needs the model |

## Mutation checks
`E2E_API_PORT=19410 E2E_WEB_PORT=19411 uv run --frozen python docs/evidence/UI-7/mutate.py` → `mutation-checks.txt`, `SUMMARY: 7 of 7 mutations caught`: M1 confirm on Review (no dialog gate), M2 Cancel confirms, M3 manager offered the button, M5 wrong proposal id → `ChatPanel.test.tsx` dialog tests; M4 citation anchor wrong → `shows the answer with each citation as a link`; M6 expired proposal offers the button → `offers no review button for an expired...`; M7 confirm does not refresh the run → `confirms exactly the proposal shown`. M1 re-run against Playwright alone: `chat.spec.ts › a proposed action ...` failed (PASS 2 / FAIL 1) (first pass, `mutation-checks` earlier run). Files restored by `git checkout`.

## Review findings left open
A reviewer subagent (code + security) found no critical or high issue. Fixed: dialog follows the stored proposal by id (stale snapshot), Escape ignored while a confirm is in flight, focus returns to the opener, stale confirm error cleared on the next ask, two-proposal and Escape tests.
- medium · `chat.spec.ts` manager test cannot fail on its button assertion, since the API never gives a manager a proposal (ADR 0024 §2). Kept as a regression check, commented; the UI guard is proved by the unit test + M3.
- low · no focus trap / inert background in the dialog (no dialog library in `ts-ui`); Escape + focus return done.
- low · a pending proposal past `expires_at` shows Review until the server reports it `failed`; the server's 409 backstops it and shows an error.
- low · an SSE `error` event is ignored (the generic "did not answer" message shows); the answer is displayed from the history refetch, not the SSE message.

## Left undone, and why
- Real-assistant answer (U6 "grounded"): needs OW-1 and CHAT-1's eval. Not run, no paid call.
- Playwright through Caddy not repeated.

## Found for other goals
- **Orchestrator / CHAT-1:** U6's row is still `open`; flip it once the real-connector eval passes.
- **E2E-1:** the e2e server's assistant is scripted; the compose path needs `AGENT_TOKEN_KEY` and `MCP_HUB_URL` for real chat.
- `RunDetail.test.tsx` manager test said "no buttons"; it now expects only the chat's "Ask" (managers may ask, FR-C1).
- `.env` copied and filled for `ob-ui-7` (ignored by git); `JAEGER_BASIC_AUTH_HASH` must be a real bcrypt (with `$$`) or Caddy is unhealthy.

## Decisions forced (ADRs written)
None.

## Traps hit
- First `make check` failed at stack start: Caddy unhealthy with a placeholder basic-auth hash; fixed with a real bcrypt.
- The output compressor filtered the first `make-check.txt`; re-captured with `rtk proxy`.
