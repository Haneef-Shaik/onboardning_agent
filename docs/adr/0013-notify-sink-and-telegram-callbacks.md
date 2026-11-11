# 0013 · The notify sink in mcp-mocks, a Telegram callback directory, and grant-only resolution

- **Status:** accepted · 2026-11-11 · goal NOTIF-1
- **Context:** PRD FR-N1/FR-N2 ask for a notification tool with an in-app and a Telegram-mock channel,
  a mock outbox, and a webhook with the real Telegram callback contract. §7.10 says a token resolves
  only at the sink, with the orchestrator's signed grant, and that a refused grant sends nothing, fails
  the task and writes a guardrail event and an audit record. REG-1 left the tool's name open
  (`notify.notify_send` in the manifest, `notify_send` in the eval cases, `notify` on the card); PII-4
  left `ensure_grant_indexes` and the growth of `pii_grant_jti` to this goal; AG-1 left the hub's
  forwarding of `X-Onb-Pii-Grant`.

## Decision

1. **The name.** Server `notify`, tool `notify_send`; hub name `notify__notify_send`; audit target
   `tool:notify.notify_send`. The arguments are one shared model, `onb_contracts.notify.NotifySendArgs`
   (`channel` = `telegram` | `in_app`; `telegram` takes `recipient_token`, `in_app` takes
   `recipient_user_id`; `body` without vault tokens). The notify server, the hub catalog and the eval
   harness's stand-in all use it; `apps/mcp-hub/tests/test_notify_agreement.py` fails when the
   manifest, the hub, the server, the eval cases or the eval scripts drift. No manifest changed.
2. **Where it runs.** The PRD fixes the 17 projects (`test_layout_has_exactly_the_17_prd_projects`)
   and puts Telegram in `mcp-mocks`, so the sink is a second MCP server in that package,
   `onb_mcp_mocks.notify` (`python -m onb_mcp_mocks.notify.main`, port 8210). It has its own
   call-context key and audience (`MCP_NOTIFY_CONTEXT_KEY`, `mcp-notify`), like every downstream
   server (ADR 0011). It holds the vault master key and the grant *verify* keys, never the signing key,
   and never a `Vault` (PII-4's `SinkResolver.create` builds one inside py-crypto).
3. **The grant travels to the sink only.** The hub reads `X-Onb-Pii-Grant` from the agent's
   connection and hands it to the downstream client only for a server in `GRANT_SERVERS` (`notify`);
   the gateway and the client both check. mcp-mongo and mcp-mocks never receive it, and a response
   that echoes it is withheld like any held credential.
4. **The org is the caller's.** The sink's tenant scope comes from the hub-signed call context, never
   from the grant (`WRONG_ORG` otherwise), as PII-4 requires.
5. **A refused grant** (`GrantRejected`, any reason) sends nothing and stores nothing in `mock_outbox`
   or `pii_access_log`. It writes, in the caller's org: the grant audit record
   (`pii.grant.resolve`, `pii_grant:<jti>`, `denied`, reason code), a guardrail decision through
   GR-1's `DecisionRecorder` (new check `sink.grant`: audit record + `guardrail.outcome` span event),
   and the bus event `task.sink_rejected` `{run_id, agent, task_id?, sink, reason}`. **The task fails
   through that event:** the orchestrator (RUN-3) subscribes and marks the task failed; until then the
   agent also gets a tool error (`grant_rejected (<reason>)`, which the hub reports as
   `downstream_denied`). Codes only, never the rejection's free-text detail.
6. **A send** first checks the grant: it must verify, be for the caller's org (else the resolver refuses
   `wrong_org`, and none of its run or task is written into the caller's org) and name the caller's run
   (else `wrong_run`, a new `RejectReason`). It then takes a **send claim** (`notify_sends`, unique per
   org and grant digest) before anything is resolved. The owner resolves the recipient once (the jti is
   single-use), hands the value to MOCK-1's transport, and in one bus transaction stores only
   `{token, masked}` in `mock_outbox`, registers any buttons, marks the claim `sent` and publishes
   `notification.sent`. A concurrent or later call with the same grant, arguments, agent and run waits
   for the owner and returns its send (`replayed: true`); anything else under that grant is a `replayed`
   refusal without touching the vault. If something fails after the value was resolved, the claim is
   marked `failed` and `task.sink_rejected` (reason `send_failed`) is published once; a retry is refused
   `send_failed`, never resolved again. (Code review: retries must not fail a task that was sent.)
7. **`pii_grant_jti` growth.** A TTL index would be single-field, and T-2's server walk fails any
   tenant index that does not start with `org_id`. Instead every successful send sweeps its own org's
   rows expired for over 5 minutes (`delete_many({expires_at < now - 5 min})`, a margin for clock drift
   between replicas, a new tenant-scoped `TenantRepository.delete_many`,
   on PII-4's `{org_id, expires_at}` index). An expired grant is refused `expired` before its jti is
   read, so a swept row can never re-enable a replay. Startup runs `ensure_grant_indexes`.
8. **Telegram callbacks.** Button `callback_data` is `ob1:` + 128 random bits (26 bytes; Telegram
   allows 64). The webhook has no tenant scope, so a new **global** collection `telegram_callbacks`
   maps a button id to its org: `{_id: callback data, org_id, created_at}` and nothing else (the
   pattern of ADR 0004's `user_logins`). The decision comes from the stored button in the org's
   `mock_outbox`, never from the request. `POST /webhooks/telegram` authenticates with
   `X-Telegram-Bot-Api-Secret-Token` (`TELEGRAM_WEBHOOK_SECRET`, 32-256 of Telegram's characters,
   compared in constant time) before the body is validated (a body that is not JSON is 400 first), accepts Telegram's `Update` (unknown
   fields ignored), answers every accepted update the same `{ok: true}`, and publishes
   `decision.received` keyed by `callback_query.id`, so Telegram's retries decide once. The payload holds
   ids and codes only: not the presser's Telegram id (a personal identifier, and in this mock the
   recipient's mobile is the chat id).
9. **In-app.** `notifications` (tenant). The consumer `notify.in_app` delivers `change_request.decided`
   (to the requester) and `run.blocked` (to the org's HR and HR_ADMIN), idempotently per (event, user).
   Its subscription map is `onb_contracts.notify.NOTIFY_SUBSCRIPTIONS`; every publisher of those
   events builds its bus with it.

## Alternatives rejected

- **A new `apps/mcp-notify` project.** Breaks the PRD's 17-project layout.
- **The notify tool as an orchestrator-local tool.** The standing rule is that agents reach tools only
  through the hub.
- **Org in the callback data or a per-org webhook URL.** The org would be a request input (C-1), and
  128-character org ids do not fit Telegram's 64 bytes.
- **A TTL index on `pii_grant_jti`.** Fails T-2's index walk (above); widening that rule is a bigger
  decision than this goal needs.

## Consequences

- RUN-3 subscribes to `task.sink_rejected` (fail the task) and HIL-1 to `decision.received` (first
  decision wins); both build their buses with `NOTIFY_SUBSCRIPTIONS` merged in.
- HIL-1 sends Approve/Reject messages with `TelegramMockChannel.send(..., buttons=…)`,
  `ref={"type": "approval", "id": …}` and `audience_user_id=<approver>`; a MANAGER's
  `GET /mock-outbox` shows only messages addressed to them.
- DEP-1 adds the `mcp-notify` service and its env (`MCP_NOTIFY_CONTEXT_KEY`, `MCP_NOTIFY_MONGO_URI`,
  `PII_MASTER_KEY`, `PII_GRANT_VERIFY_KEYS`), the hub's `MCP_NOTIFY_CONTEXT_KEY` and
  `MCP_HUB_NOTIFY_SERVER_URL`, and the API's `TELEGRAM_WEBHOOK_SECRET`. MCP-3/SEC: the sink's database
  user needs read on `pii_vault`, `org_keys`, `users` and write on the collections listed in
  `onb_mcp_mocks.notify.settings`.
- The mock uses the normalised mobile as the Telegram `chat_id`. A real Bot API adapter cannot send
  to a phone number: it needs a chat id from the bot's own `/start` flow (a gap for the real adapter).
- Open for HIL-1 / SEC-2: a press is not bound to the expected chat, and buttons never expire; agents
  may send in-app messages to any user of their org without a rate cap.
- One more global collection: it reveals to someone with database read access which org a button id
  belongs to, nothing else.
