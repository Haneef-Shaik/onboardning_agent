# Checkpoint — NOTIF-1

Branch `goal/notif-1-notifications-sink` (from main 5f9d3e5). Compose project `ob-notif-1`, Caddy 19400/19401.

## Done
1. `onb_contracts.notify`: tool args/result, Telegram `Update`/`CallbackQuery`, `ob1:` callback data,
   event names, `NOTIFY_SUBSCRIPTIONS`, outbox API model. py-data: `delete_many`, global
   `telegram_callbacks`. py-guardrails: `GuardrailCheck.SINK_GRANT`. (8f7c60e)
2. `onb_mcp_mocks.notify`: `NotifySink` (telegram via PII-4 resolver, masked outbox, replay by grant
   digest, jti sweep, rejection → grant audit + guardrail + `task.sink_rejected`), in-app channel and
   `notify.in_app` consumer. Unit tests 28 (box 1, box 2 ×7 cases). (78caef1)

## Next
3. notify MCP server (`onb_mcp_mocks.notify.server`, audience `mcp-notify`, reads `X-Onb-Pii-Grant`),
   settings, main (+ in-app worker).
4. Hub: `notify_catalog`, forward grant header to notify only, settings `MCP_NOTIFY_CONTEXT_KEY`,
   `MCP_HUB_NOTIFY_SERVER_URL`.
5. API: `GET /mock-outbox`, `POST /webhooks/telegram`; contracts regen.
6. tests_xt rows; evals toolbox on the shared contract; tests_int; mutations; ADR 0013; handoff.

## Decisions / surprises
- PRD fixes 17 projects (`test_layout_has_exactly_the_17_prd_projects`) and puts Telegram in
  mcp-mocks → the notify server lives in `apps/mcp-mocks` as a second server.
- TTL index on `pii_grant_jti` would fail T-2's "every tenant index starts with org_id" walk → sweep
  per send instead (tenant-scoped `delete_many`).
- Heredocs are refused by the session shell; use Edit/perl.
