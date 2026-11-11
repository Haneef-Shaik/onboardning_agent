# Checkpoint — MOCK-1 · goal/mock-1-integration-mocks

## Steps done
1. `call_context`: `audience` keyword on sign/verify (default `mcp-mongo`).
2. `apps/mcp-mocks`: contracts, idempotent store (per-org quota), deterministic tools, FastMCP server
   behind hub-signed contexts (audience `mcp-mocks`, own key), Telegram transport stub, settings, main.
3. Hub: `mocks_catalog()` / `platform_catalog()`, `McpDownstream(..., keys=...)` failing closed,
   settings `MCP_MOCKS_CONTEXT_KEY` + `MCP_HUB_MOCKS_SERVER_URL`, main serves every platform tool.
4. Integration tests (`apps/mcp-hub/tests_int/test_hub_mocks_int.py`), hub xt cases, `tools/tests_xt`
   registration (`MCP_SERVERS`, `MCP_SERVER_MODULES`, `TOOL_CASES`).
5. ADR 0011; code + security review (no critical/high; mediums fixed); mutations 21/21.

## Next step
Gates on the final code (`make check`, `make test-int`), box evidence, handoff, goal-verifier.

## Surprises
- A 24-hex idempotency key under a non-`*_id` name trips the hub's raw-PII check ~1% of the time:
  the key is `task_id`.
- FastMCP's session manager runs once per instance: the hub unit Stack builds a fresh mocks server per
  call over the same store.

## Status (2026-11-11)
In review after goal-verifier pass (attempt 1, code at 736abd9). Held for the orchestrator: no rebase,
merge or branch deletion from this session.
