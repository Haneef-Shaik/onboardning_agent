# 0026 · The mock outbox's buttons: an authenticated press that calls the Telegram webhook in-process

- **Status:** accepted · 2026-11-13 · goal UI-6
- **Context:** PRD FR-N2 wants the mock outbox's Approve/Reject buttons to "hit the same webhook endpoint a real
  Telegram callback would — so swapping in the real adapter later only changes the adapter", and UI-6's box 2
  says the button "calls `POST /webhooks/telegram`". That webhook authenticates with Telegram's secret token
  (`TELEGRAM_WEBHOOK_SECRET`, ADR 0013), which must never reach the browser (NOTIF-1's finding for UI-6). The
  webhook takes no user token, so a browser press made straight to it could not say who pressed.

## Decision

1. **A new route, `POST /mock-outbox/{message_id}/press` `{callback_data}`**, for any signed-in role. It reads the
   message in the token's org and requires `audience_user_id == the caller` and the button to be on that message;
   anything else (another user's message, a message with no audience, another org's, a button from another
   message) is **404**, the answer for a message that does not exist. A message whose `ref` is an approval that is
   no longer `pending` is **409** "This approval has already been decided." (an early answer only: the decision
   itself stays first-wins in `ApprovalDecider`, ADR 0020 §4).
2. **The route then calls `POST /webhooks/telegram` itself, in-process**: one ASGI HTTP request through the whole
   app (`onb_api.notify.relay.post_json`: middleware, the secret check, body validation, the webhook handler), with
   the secret header and the Bot API `Update` a real tap sends (`callback_query.data` = the button id, a fresh
   `callback_query.id` per tap, no Telegram identity of the presser). It runs in an **empty context**, so the
   caller's tenant scope and span do not leak into the webhook (which takes its org from the button directory).
   A non-2xx answer is 503. No network, no configured URL, no new dependency.
3. The decision is then exactly a Telegram press: `decision.received` → `orchestrator.record_decision` attributes
   it to the message's `audience_user_id` (which the route has just checked is the caller), `channel = telegram`.
4. `GET /mock-outbox` messages carry `audience_user_id`, so the UI shows the buttons only to the user they are for.

## Alternatives rejected

- **The browser posts to the webhook.** Needs the secret in the browser.
- **The press route calls `MockChannels.handle` directly.** Same effect, but not "the same webhook endpoint": the
  secret check and the `Update` validation would be skipped, and a change to the webhook route would not be
  exercised by the UI.
- **An HTTP call to the API's public URL.** Needs a configured self-URL, leaves the process, and depends on Caddy.

## Consequences

- One more guarded route (role matrix, xt row). The webhook secret stays server-side; tests assert it is in no
  response.
- Each press is its own callback query (like two taps in Telegram): a double press publishes twice and the second
  decision loses (`AlreadyDecidedError`, logged), as with the real channel.
- A real Telegram adapter changes nothing here: real presses arrive at the same route from Telegram, and the mock
  outbox can be dropped or kept for demos.
