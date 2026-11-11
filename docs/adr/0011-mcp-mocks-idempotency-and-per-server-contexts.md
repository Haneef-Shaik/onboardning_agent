# ADR 0011 — mcp-mocks: idempotency on the task id, one call-context key and audience per server, and the Telegram stub seam

- **Status:** accepted (MOCK-1), 2026-11-11
- **Goal:** MOCK-1 · PRD §2 Non-goals (mocks), §6.6, §7 diagram (`mcp-mocks`), §7.4, §7.10; HUB-1 handoff, ADR 0006

## Context

MOCK-1 puts thin Bitbucket and IT-provisioning mocks behind the hub and gives NOTIF-1 a Telegram
transport stub. Four things had to be decided:

1. What the idempotency key is, and how it reaches the mock.
2. How the mock server learns the caller's org, and with which credential.
3. Where the mock records live.
4. Whether the Telegram stub is an MCP tool.

## Decision

1. **The run task's id is the idempotency key.** Each mock tool takes a required `task_id` (24-hex
   ObjectId). A record is keyed by (org, tool, `task_id`); the first call creates it, a replay with the
   same arguments returns the stored result and creates nothing, and the same key with other arguments
   is refused (`idempotency_conflict`, which the hub records as `downstream_denied`). The argument is
   named `task_id`, not `idempotency_key`, because the hub's raw-PII argument check exempts hex ids only
   under `*_id` keys (HUB-1 `pii.py`); a random 24-hex key under any other name is refused as raw PII
   about 1% of the time (measured: 211 of 20 000 under `idempotency_key`, 0 of 20 000 under `task_id`).
   No hub guard was loosened.
2. **One key and one audience per downstream server.** The hub signs mocks contexts with
   `MCP_MOCKS_CONTEXT_KEY` for audience `mcp-mocks`, and mongo contexts with `MCP_CALL_CONTEXT_KEY` for
   `mcp-mongo` (`McpDownstream(..., keys={"mocks": ...})`, audience `mcp-<server>`). The hub refuses to
   start unless `AGENT_TOKEN_KEY`, `MCP_CALL_CONTEXT_KEY` and `MCP_MOCKS_CONTEXT_KEY` all differ. A mocks
   context is refused by mcp-mongo and the reverse, and a compromised mocks server cannot mint mongo
   contexts. `sign_call_context` / `verify_call_context` take an `audience` argument (default `mcp-mongo`,
   so mcp-mongo is unchanged). `McpDownstream` fails closed: every server with a URL other than mongo must
   have its own key, and no two servers share a key. The org comes only from the verified context, never
   from arguments.
3. **Mock records are in memory** (`InMemoryIdempotencyStore`, a quota of 10 000 records per org, so one
   org filling it never blocks another; a full org gets a `store_full` refusal and its replays still
   answer). Ids are SHA-256 of (org, tool, `task_id`, argument fingerprint), so a replay after a restart
   still returns the same ids and the same key with other arguments gets other ids; only the conflict
   refusal is lost across a restart. `groups` are sorted, so their order never makes a replay a conflict.
   The mocks are thin (PRD §2) and touch no MongoDB, so they
   need no database user and no `TenantRepository`.
4. **The Telegram stub is a Python transport, not an MCP tool.** `onb_mcp_mocks.telegram.TelegramTransport`
   is the seam NOTIF-1's `telegram_mock` sink calls after resolving the vault token; `TelegramMockTransport`
   checks Bot API limits (text 1–4096 characters, callback data 1–64 bytes, a non-zero chat id or an
   `@username`), returns a Bot-API-shaped
   receipt, and keeps no copy of the recipient or text. Raw values must never cross the hub, whose argument
   check would refuse them anyway.

## Consequences

- REG-1's manifest names (`mocks.create_bitbucket_account`, `mocks.provision_laptop`) are served as
  `mocks__create_bitbucket_account` and `mocks__provision_laptop`; no YAML changed.
- Deployment (DEP-1) must set `MCP_MOCKS_CONTEXT_KEY` on both the hub and mcp-mocks, and
  `MCP_HUB_MOCKS_SERVER_URL` on the hub. mcp-mocks listens on 8200 by default.
- The orchestrator / AGT-1 passes the run task's id as `task_id`; retries of a task are then safe.
- NOTIF-1 stores the masked copy in `mock_outbox` itself; the stub's receipt holds only `message_id` and
  `date`.
