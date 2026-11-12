# Handoff — NOTIF-1 · Notify in-app and through the Telegram mock, resolving PII only at the sink        attempt 2 · goal/notif-1-notifications-sink · see `git log -1`

**Outcome claimed.** `notify_send` (server `notify`, hub `notify__notify_send`) sends through two
adapters:
- `in_app`, which writes a `notifications` record;
- `telegram` (the Telegram mock), whose sink:
  - verifies the orchestrator-signed grant, which the hub forwards to this server only;
  - resolves the token through PII-4's resolver;
  - "sends" through MOCK-1's transport;
  - stores only `{token, masked}` in `mock_outbox`.

A refused grant sends and stores nothing. It writes an audit record and a guardrail decision, and it
publishes `task.sink_rejected`, which fails the task.

`GET /mock-outbox` and `POST /webhooks/telegram` follow Telegram's callback contract. The webhook checks
the secret-token header, accepts an `Update` with `callback_query`, and publishes `decision.received`.
Decisions: `docs/adr/0013-notify-sink-and-telegram-callbacks.md`.

## Attempt 2: rebased onto main (HIRE-2, SEED-1, UI-8)
Attempt 1 passed the verifier. Main then moved, and I rebased by hand (as the orchestrator authorised).
The 16 commits are replayed unchanged, and these conflicts were resolved by keeping both sides:

- **`apps/api/src/onb_api/app.py`:** `create_app` keeps main's `templates`, `documents`, `evals` and `llm`. `notify` is a new parameter at the end of each list: parameters, lifespan `ensure_indexes`, `app.state`, routers.
- **`apps/api/src/onb_api/main.py`:** keeps main's `CONFIG_DIR`, LLM resolution, `TemplateLifecycle`, `DocumentService` and `EvalRunStore`. It adds `MockChannels` (needs `TELEGRAM_WEBHOOK_SECRET`) and passes `notify=` to the one `create_app` call.
- **`apps/api/tests/conftest.py`, `test_auth_settings.py`, `test_role_matrix.py`:** the new `notify` fixtures sit beside main's fixtures. The webhook route stays in `PUBLIC_ROUTES`, which main's new body-field test also honours. My two startup tests now pass `CONFIG_ENV`, because main requires `CONFIG_DIR` first.
- **`tools/tests_xt/`:** main reworked `RouteCase.check` to take `unscoped`, so my two checks are wrapped in main's `_reads_only`. `build_api` builds main's full app plus `notify`. My route-case block is grafted into main's file.
- **`libs/ts-api-client/*`:** generated; `make contracts` regenerated both files on the rebased tree.
- **SEED-1's `.env.example` vars:** no conflict. They feed `make seed` only, and no gate needs them.

I re-ran every gate on the rebased tree (below). Nothing about the design or the claims changed.

Main's `BodyLimitMiddleware` (UI-8/TPL-2) caps bodies on `/templates` only, so the webhook's body-size
LOW below is still open. Extending that middleware's `_LIMITED_PREFIX` check to `/webhooks` would close it.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `mock_outbox` holds only the masked mobile, never the raw value (DB scan test) | yes | **Unit:** `apps/mcp-mocks/tests/test_notify_sink.py::test_box1_mock_outbox_holds_only_the_masked_mobile_never_the_raw_value`. It runs the real vault, grants, resolver and tenant scoping over the in-memory driver. It scans every document of every collection, in every org, except `pii_vault` and `org_keys`, for the raw mobile and its hidden middle digits, and finds none. The stored recipient is `{token, masked: "98XXXXXX34"}`, with one masked `pii_access_log` entry.<br>**Live:** `apps/mcp-mocks/tests_int/test_notify_against_mongo.py::test_box1_…_db_scan`. It runs the production `build_service` wiring on the compose replica set, called over MCP with a hub-signed context. The root scan of every collection is clean. The outbox write and `notification.sent` commit together.<br>**Command:** `bash docs/evidence/NOTIF-1/box-tests.sh` → `docs/evidence/NOTIF-1/box-tests.txt`. Box 1 `2 passed`; `exit=0`.<br>**Mutation N1:** storing the raw mobile instead of the mask is caught by both box-1 tests. |
| A rejected grant stores nothing, fails the task, and writes a guardrail event and an audit record (test) | yes | **Unit:** `test_notify_sink.py::test_box2_a_rejected_grant_stores_nothing_fails_the_task_and_writes_a_guardrail_event_and_an_audit_record`, with 7 cases: no grant, forged signature, wrong sink, another hire's token, another org's token, another org's grant, an email token used as recipient. Each case asserts:<br>• nothing sent (transport count 0);<br>• `mock_outbox`, `pii_access_log` and `notifications` empty;<br>• no raw value anywhere;<br>• the task fails: exactly one `task.sink_rejected` `{run_id, agent, sink, reason}`;<br>• audit records `pii.grant.resolve`/denied/reason and `guardrail.sink.grant`/denied/reason, in the caller's org only;<br>• a `guardrail.outcome` span event (`guardrail.check=sink.grant`).<br>**Live:** `tests_int/…::test_box2_…`, 4 cases (another hire's token, another org's token, another org's grant, no grant), on the real bus and `AuditLog`.<br>**Server:** `test_notify_server.py::test_a_rejected_grant_is_a_refusal_naming_the_reason_never_an_internal_error`. The tool answers `grant_rejected (<reason>): nothing was sent`.<br>**Command:** `box-tests.sh`, Box 2 `12 passed`.<br>**Mutations:** N2 (no guardrail), N3 (no task failure event), N4 (no audit record) and N5 (refusal swallowed) are each caught. |
| The webhook rejects a wrong secret token, and accepts a payload matching Telegram's documented `callback_query` (fixture test) | yes | **Fixture:** `apps/api/tests/fixtures/telegram_callback_query_update.json`, a Bot API `Update` with `callback_query` `{id, from, message{message_id, from, chat, date, text, reply_markup.inline_keyboard}, chat_instance, data}`.<br>**Tests:**<br>• `apps/api/tests/test_notify_routes.py::test_box3_the_webhook_rejects_a_wrong_secret_token[missing, empty, another-secret, one-character-short]`: 401 `UNAUTHENTICATED`, nothing published;<br>• `::test_box3_a_wrong_secret_is_refused_before_the_body_is_validated`;<br>• `::test_box3_the_webhook_accepts_telegrams_documented_callback_query_and_emits_a_decision_event`: 200 `{ok: true}`, and `decision.received` published in the button's org with the decision of the stored button, keyed `telegram:callback:<callback_query.id>`.<br>**Command:** `box-tests.sh`, Box 3 `6 passed`.<br>**Mutation N11:** the secret check removed fails all 4 wrong-secret tests. |
| `make test-xt` covers every new route | yes | **Command:** `make test-xt` → `docs/evidence/NOTIF-1/make-test-xt.txt`, also part of `make check` in `make-check.txt`. Cross-tenant surface: 26 API routes (including `GET /mock-outbox` and `POST /webhooks/telegram`), 9 MCP tools (including `mcp-notify.notify_send`), 1 event consumer (`notify.in_app`). tools/tests_xt `239 passed` in the rebased run (main's routes included), `hub tools covered: … notify__notify_send`.<br>**Rows added:**<br>• `ROUTE_CASES`: org-A sees only its outbox; a manager sees only messages addressed to them; an org-B button press decides in org B only and answers like an unknown button; a wrong secret is 401;<br>• `TOOL_CASES` (`mcp-notify.notify_send`): another org's token answers like an absent token, another org's grant is `wrong_org`, an in-app message to an org-B user answers like one to nobody;<br>• `CONSUMER_CASES` (`notify.in_app`);<br>• `HUB_TOOL_CASES`;<br>• `ROLE_MATRIX`: `GET /mock-outbox` is ALL_200, and the webhook is a public route. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Name and contract.** Server `notify`, tool `notify_send`, hub name `notify__notify_send`, audit target `tool:notify.notify_send`. The arguments are `onb_contracts.notify.NotifySendArgs`: `channel` `telegram` (with `recipient_token`) or `in_app` (with `recipient_user_id`), plus a `body` with no `ptk_`. The manifest, hub, server, eval dataset, eval scripts and eval toolbox all agree; no manifest changed. | `tools/tests/test_notify_agreement.py` |
| **Grant forwarding.** The agent runtime's `X-Onb-Pii-Grant` reaches only servers in `GRANT_SERVERS = {notify}`. The gateway and `McpDownstream` both check this. A result that echoes the grant is withheld. | `apps/mcp-hub/tests/test_notify.py` (mutations N15–N17) |
| **Running the sink.** `python -m onb_mcp_mocks.notify.main`, port 8210. Env: `MCP_NOTIFY_CONTEXT_KEY` (audience `mcp-notify`), `MCP_NOTIFY_MONGO_URI`, `PII_MASTER_KEY`, `PII_GRANT_VERIFY_KEYS`, optional `PII_SINK_POLICY_FILE`. Startup runs `ensure_grant_indexes` plus the outbox, send-claim, in-app and bus indexes. | `apps/mcp-mocks/tests/test_notify_main.py` |
| **Hub settings.** The hub needs `MCP_NOTIFY_CONTEXT_KEY` and `MCP_HUB_NOTIFY_SERVER_URL`. All 4 keys must differ. | `apps/mcp-hub/tests/test_main.py` |
| **Grant checks at the sink.** The org comes from the hub context only. A grant for another org is `wrong_org`. A grant for another run than the context names is `wrong_run` (a new `RejectReason`), audited like the others. | `test_notify_sink.py::test_the_caller_org_comes_from_the_context_not_the_grant`, `…_another_run_…` (N19, N20) |
| **One send per grant (`notify_sends`).** The same grant, arguments, agent and run gives the first send back (`replayed: true`), even when the calls run concurrently. A used grant with other arguments is `grant_rejected (replayed)`. A failure after resolution is reported once (`task.sink_rejected`, reason `send_failed`), and a retry of it is refused `send_failed`. | `tests_int/…::test_concurrent_retries_of_one_call_send_once_and_fail_no_task`, `test_concurrent_uses_of_one_grant_send_once`, unit tests (N6, N18) |
| **Events.** `task.sink_rejected` `{run_id, agent, task_id?, sink, reason}`: RUN-3 fails the task. `decision.received` `{ref_type, ref_id, decision, channel, outbox_id, callback_query_id}`: HIL-1. `notification.sent` `{outbox_id, channel, run_id, task_id}`. `NOTIFY_SUBSCRIPTIONS` (`change_request.decided`, `run.blocked` → `notify.in_app`) must be merged into any publisher's bus. | sink, route and consumer tests |
| **Approve/Reject buttons for HIL-1.** `TelegramMockChannel.send(chat_id, OutboxEntry(..., ref={"type": "approval", "id": …}, audience_user_id=<approver>), buttons=[DecisionButton(...)])`, then `store(document)` inside your transaction. Buttons get opaque `ob1:` ids in the global `telegram_callbacks`. | `tools/tests_xt/test_xt_api.py` webhook case; `tests_int/…::test_a_telegram_press_…` |
| **In-app consumer.** `change_request.decided` `{change_request_id, requested_by, decision}` notifies the requester. `run.blocked` `{run_id, task_id}` notifies the org's HR and HR_ADMIN. It is idempotent per (event, user). | `test_notify_in_app.py`, `tests_int/…::test_a_blocked_run_…` |
| **API.** `create_app(..., notify=MockChannels(store, webhook_secret=..., decisions=BusDecisions(store)))`. `onb_api.main` requires `TELEGRAM_WEBHOOK_SECRET`: 32–256 characters of `[A-Za-z0-9_-]`. | `test_auth_settings.py` |
| **`TenantRepository.delete_many`.** Tenant-scoped. `telegram_callbacks` is in `GLOBAL_COLLECTIONS`. | py-data unit and int tests |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U5 @ MS-6 | compose, 2026-11-12 | contributes: the approver's Telegram-mock buttons reach the webhook and publish a decision | `box-tests.txt`, `make-test-xt.txt` | no (MS-6 is the last prover) |
| NF-PII @ MS-6 | compose, 2026-11-12 | contributes: masked-only outbox (DB scan); the sink refuses unverified, wrong-org, wrong-run, other-hire and replayed grants | `box-tests.txt`, `mutation-checks.txt` | no (MS-6) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, including test-xt (`docs/evidence/NOTIF-1/make-check.txt`) |
| Integration | `make test-int` | `Successfully ran target test-int for 9 projects`, exit 0 (`make-test-int.txt`); mcp-mocks tests_int `11 passed` |
| Cross-tenant | `make test-xt` | `cross-tenant surface: 26 API routes, 9 MCP tools, 1 event consumers`; `239 passed`; `hub tools covered: … notify__notify_send` (`make-test-xt.txt`; the first run hit an environmental Mongo `connection closed` and was rerun) |
| Contracts | `make contracts`, then `make contracts-check` | `openapi.json and the TS client are up to date` (regenerated on the rebased tree, both files committed) |
| Evals | `make evals-selftest` | `27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8` (`make-evals-selftest.txt`, rebased tree) |
| Mutations | `uv run --frozen python docs/evidence/NOTIF-1/mutate.py` | `20/20 killed`, `0 files differ` (`mutation-checks.txt`) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| PII-4 | `SinkResolver.create(...)`; `resolve_at_sink` is all or nothing; tenant scope comes from the caller's context; run `ensure_grant_indexes` at startup | yes. Resolver tests run through it, and startup calls it (`test_notify_main.py`). |
| PII-4 | `pii_grant_jti` needs a TTL or sweep | yes, handled by a sweep. Each send deletes its org's rows that expired more than 5 minutes ago, using `{org_id, expires_at}`. |
| MOCK-1 | `TelegramMockTransport.send_message` keeps no copy; `InlineButton` callback data ≤ 64 bytes; per-server key and audience in `McpDownstream(keys=)` | yes |
| AG-1 | the grant travels as the `X-Onb-Pii-Grant` header | yes. The hub now forwards it to notify only (`test_the_grant_header_is_the_one_the_agent_runtime_sends`). |
| EVT-1 | `publish(change=…)` is transactional; handlers are at least once | yes. The outbox, claim, button registrations and event commit together; the consumer dedupes. |
| GR-1 | `DecisionRecorder` writes the audit record and the span event | yes, with the new check `sink.grant` |
| T-3 | `ROUTE_CASES`, `TOOL_CASES`, `MCP_SERVERS`, `CONSUMER_CASES`, `BUS_MODULES` | yes. All registered; the coverage tests pass. |

## Mutation checks
`docs/evidence/NOTIF-1/mutation-checks.txt`, 20/20 killed:

- **N1** raw mobile stored → both box-1 tests fail.
- **N2** no guardrail record → box2 tests fail.
- **N3** no task.sink_rejected → box2 tests fail.
- **N4** no grant audit record → box2 tests fail.
- **N5** refusal swallowed → box2 and server rejection tests fail.
- **N6** claim conflict ignored → `test_the_same_grant_with_other_arguments_is_a_rejected_replay` fails.
- **N7** sweep deletes live rows → the sweep test fails.
- **N8** token in body allowed → `test_a_body_carrying_a_token_is_refused…` fails.
- **N9** context may name another tool → the server test fails.
- **N10** in-app without dedupe → the redelivery test fails.
- **N11** webhook secret unchecked → box3 wrong-secret tests fail.
- **N12** press decided in a fixed org → `test_a_press_on_an_org_b_button_decides_in_org_b_only` fails.
- **N13** decision taken from the request → `test_the_decision_comes_from_the_stored_button…` fails.
- **N14** manager sees the whole outbox → the manager test fails.
- **N15** gateway gives the grant to every server → gateway test fails.
- **N16** downstream sends the grant header to every server → `test_the_grant_header_never_reaches_another_server[*]` fails.
- **N17** echoed grant not withheld → the echo test fails.
- **N18** failure after resolution not recorded → the `send_failed` test fails.
- **N19** run not compared → the `wrong_run` test fails.
- **N20** another org's claims recorded → the cross-org claims test fails.

## Review findings left open
A code review and a security review (general-purpose subagents) ran on `git diff main...HEAD`. Neither
found a CRITICAL. Fixed:

- **Code review HIGH:** a concurrent retry failed a task that was sent. Now one send claim per grant.
- **Code review HIGH:** a failure after resolution locked the task. Now `send_failed` is reported once and a retry is refused.
- **MEDIUMs, both reviews:**
  - replay skipped grant verification;
  - buttons were registered outside the transaction;
  - the docstring claimed the UI posts to the webhook;
  - one bad outbox document caused a 500;
  - the grant's run was not checked (now `wrong_run`);
  - the presser's Telegram id was in the event (in the mock, that id is the recipient's mobile).
- **LOWs:**
  - another org's claims written into the caller's org;
  - the jti sweep had no clock margin;
  - an in-app dedupe race returned an empty id;
  - a dead channel check in the eval toolbox.

Open:

- **LOW · `apps/api/src/onb_api/notify/routes.py` · JSON before the secret.** FastAPI reads and parses the JSON body before the secret dependency runs, so a non-JSON body with no secret gets 400 rather than 401, and the body size is not capped. A schema-invalid JSON body is 401, and the docstring says so. Fix in DEP-1/SEC-2: cap the body size at Caddy, or check the secret in middleware.
- **LOW · `service.py` · Presses unbound.** A press is not bound to the expected chat, and buttons never expire. For HIL-1/SEC-2: bind the chat with a keyed hash and add `expires_at`.
- **LOW · `sink.py` · In-app unthrottled.** Agents can send in-app messages to any user of their org with no rate cap. For SEC-2.
- **LOW · `sink.py` · Chat id is the mobile.** The mock uses the normalised mobile as the `chat_id`. A real Bot API adapter needs a chat id from the bot's `/start` flow. Recorded in ADR 0013.
- **LOW · Index defined twice.** The API's `MockChannels.ensure_indexes` repeats the sink's outbox `sent_at` index. It is identical, so harmless, and it keeps the API's listing indexed even without mcp-notify.

## Left undone, and why
- **`/goal`:** this background session cannot set it. I worked to the text that `plan.py goal NOTIF-1` printed in the session.
- **Compose service and `.env.example` entries for mcp-notify:** no compose service runs the hub or the mocks yet (DEP-1). The env-check covers compose variables only.
- **Producers of `change_request.decided` and `run.blocked`:** these come from TB-2 and RUN-2. The consumer and its event contract are ready.
- **A `GET /notifications` route:** it is not in the PRD §10 API surface. The in-app records are stored for the UI goals to read.

## Found for other goals
- **RUN-3:**
  - subscribe to `task.sink_rejected` and fail the task;
  - issue grants with the run's own `run_id` (the sink refuses `wrong_run`);
  - pass the grant only as the header.
- **HIL-1:**
  - subscribe to `decision.received`; first decision wins;
  - send approvals with `TelegramMockChannel` and `audience_user_id`;
  - map the presser through the stored audience, not Telegram ids.
- **UI-6:** do not ship `TELEGRAM_WEBHOOK_SECRET` to the browser. The mock outbox's Approve/Reject needs an authenticated route that calls the same `MockChannels.handle`, after checking `audience_user_id == principal.user_id`.
- **DEP-1:**
  - add the `mcp-notify` service (env above);
  - give the hub `MCP_NOTIFY_CONTEXT_KEY` and `MCP_HUB_NOTIFY_SERVER_URL`;
  - give the API `TELEGRAM_WEBHOOK_SECRET`;
  - cap webhook bodies at Caddy.
- **MCP-3 / deploy role:** the sink's database user reads `pii_vault`, `org_keys` and `users`. It writes `mock_outbox`, `notifications`, `notify_sends`, `pii_grant_jti` (insert and delete), `pii_access_log`, `audit_log`, `events`, `jobs` and `telegram_callbacks`.
- **SEC-2:** the open LOWs above.

## Decisions forced (ADRs written)
- **`docs/adr/0013-notify-sink-and-telegram-callbacks.md`:**
  - the name and the one shared contract;
  - the sink as a second server in mcp-mocks (the PRD fixes 17 projects);
  - the grant forwarded to notify only;
  - a refusal fails the task through `task.sink_rejected`;
  - the send claim;
  - the jti sweep instead of a TTL index (T-2's org_id-prefix walk);
  - the global `telegram_callbacks` directory and the webhook contract;
  - the in-app consumer.
- **Small shared-lib additions:**
  - `TenantRepository.delete_many` (py-data);
  - `GuardrailCheck.SINK_GRANT` (py-guardrails);
  - `RejectReason.WRONG_RUN` (py-crypto);
  - `GLOBAL_COLLECTIONS += telegram_callbacks`.

## Traps hit
- **PRD project count.** `test_layout_has_exactly_the_17_prd_projects` rules out a new project.
- **Vault import boundary.** PII-2's scan forbids importing `Vault` outside the API, so tests seed through `onb_api.testing_mongo.seed_vault_value`.
- **Eval harness boundary.** Apps may not import `onb_evals`, so the agreement test lives in `tools/tests`.
- **Shared `tests` package name.** Running pytest on several projects in one session fails on the shared `tests` package name. Run each project separately; Nx does this.
- **Notify int test seeding.** The notify int test seeds on its own Mongo client, because an async client stays on its first event loop.
- **Hub int flake under load.** Under parallel `make test-int` load, `test_hub_mocks_int::test_int_every_registered_agent_without_the_mock_tool_is_denied` once found no span. It passed alone, and the full rerun was green.
- **Unrelated Mongo reconnect.** `make evals-selftest` once failed on a Mongo `AutoReconnect` that had nothing to do with this change.
- **Session shell.** It refuses heredocs and complex git compounds; use Edit, perl, or `$CLAUDE_JOB_DIR/tmp/run-env.sh`.
