# Checkpoint — HUB-1 · goal/hub-1-mcp-hub

## Done
1. `onb_agents.tokens`: agent token issue/verify, `hub_client_config` (agent-side config).
2. `onb_mcp_hub.catalog` + `policy`: hub tools `mongo__<tool>`, per-call authorization (manifest,
   scopes, JSON schema, `org_id`, collections, provisioned users, vault hard-deny, mcp-mongo
   validator pre-check, approvals).
3. `onb_mcp_hub.scan`: PII redaction, credential block, non-text block.
4. `onb_mcp_hub.gateway` + `downstream` + `server` + `settings` + `main`: full pipeline over MCP.
5. `apps/mcp-hub/tests_int`: testcontainers Mongo, real mcp-mongo with MCP-3 users, real registry
   and `AuditLog`; `test_hub_xt.py` = cross-tenant case per hub tool; `make test-xt` target.
6. py-evals runner reads hub `AuditLog` records; ADR 0008.

## Next
Code + security review, mutation checks, gates (`make check`, `make test-int`, `make test-xt`,
`make evals-selftest`, `make contracts-check`), handoff, verifier, board → in review, `make down`.

## Surprises
- The PRD's PII detectors match digit runs inside ObjectId hex and vault tokens: the scan skips
  whole-string hex ids and `ptk_` tokens.
- `AgentRegistry.sync` needs a replica set (transactions); the int suite writes registry records in
  sync's exact shape instead.
- T-3's `make test-xt` (not on main) runs `tools/tests_xt`; this branch's target fans out to nx
  `test-xt` targets. Merge both recipes.
