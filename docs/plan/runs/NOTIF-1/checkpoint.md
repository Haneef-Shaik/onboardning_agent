# Checkpoint — NOTIF-1

Branch `goal/notif-1-notifications-sink` (from main 5f9d3e5). Compose project `ob-notif-1`, Caddy 19400/19401.
Stopped on a usage limit. All implementation and review fixes are committed.

## Done
- The build is complete. See ADR 0013, `docs/adr/0013-notify-sink-and-telegram-callbacks.md`, for what was built and why.
- Code review and security review were run; their HIGH and MEDIUM findings are fixed:
  - one send claim per grant (`notify_sends`);
  - a run that already sent is never failed, and a failure after the value was resolved is reported once as `send_failed`;
  - a grant for another run is refused (`wrong_run`);
  - no presser Telegram id in `decision.received`;
  - the jti sweep keeps a 5-minute margin;
  - buttons are registered inside the send transaction;
  - unreadable outbox documents are skipped.
- Evidence in `docs/evidence/NOTIF-1/`:
  - `box-tests.txt`: boxes 1–3, unit and compose-Mongo integration, exit 0.
  - `make-test-xt.txt`: box 4. 14 routes, 9 MCP tools and 1 consumer; tools/tests_xt 152 passed; hub cross-tenant suite includes `notify__notify_send`.
  - `make-check.txt`: `make check: all passed`.
  - `make-test-int.txt`: 9 projects, exit 0.
  - `mutation-checks.txt`: 20/20 killed, 0 files differ.
- `make contracts-check`: up to date.

## Next
1. `make evals-selftest` failed with `pymongo AutoReconnect: connection closed`. That is an infrastructure failure, not an eval result. Rerun it; the first rerun should be alone, without other suites. The notify toolbox change is covered by the py-evals unit tests (261 passed).
2. Write `handoff.md`. Its sections:
   - **Box rows:** the evidence above.
   - **Open LOWs:**
     - The webhook parses JSON before the secret check (400 vs 401). Add a body size cap at Caddy.
     - Button presses are not bound to a chat or a TTL (HIL-1/SEC-2).
     - Agents can send unthrottled in-app messages (SEC-2).
     - The chat id is the mobile number in the mock (real adapter gap).
     - The API duplicates the outbox `sent_at` index.
   - **Dependents** (see the ADR's Consequences section):
     - RUN-3 subscribes to `task.sink_rejected`.
     - HIL-1 subscribes to `decision.received` and uses `TelegramMockChannel.send(buttons=…, audience_user_id=…)`.
     - UI-6 must not ship the webhook secret to the browser; it needs an authenticated decide route.
     - DEP-1 needs the env vars listed in the ADR.
3. Run the goal-verifier. Then set the board to `in review`. Run `make down`.

## Traps
- Heredocs and complex compound git commands are refused by the session shell. Use Edit/perl. `$CLAUDE_JOB_DIR/tmp/run-env.sh` loads `.env`.
- Run pytest per project: the shared `tests` package name clashes otherwise.
- Apps may not import `onb_evals`. The agreement test lives in `tools/tests`.
- Under parallel `make test-int` load, `test_hub_mocks_int` can flake (span capture). It passes alone.
