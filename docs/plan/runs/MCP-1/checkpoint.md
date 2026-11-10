# Checkpoint — MCP-1 · Serve MongoDB tools over MCP with strict contracts

Branch `goal/mcp-1-mongo-mcp-server`, from main @ 672c5e6. Compose project `ob-mcp-1` (ports 19200/19201).

## Steps done
1. Deps: `mcp` 1.30 (official SDK FastMCP), `pyjwt`, `pydantic`, `uvicorn`, `onb-data`, `onb-telemetry`;
   dev `testcontainers` 4.15 (75ed7a9).
2. Call context (HS256 JWS from the hub, header `X-Onb-Call-Context`), policy (`org_id` anywhere →
   rejected, read/write collections from the context, stage allowlist), closed contracts (a572689).
3. Server: `TracedTool` (FastMCP `Tool` subclass) verifies the context, validates against the closed
   contract, runs the handler in `tenant_scope`, one span per call (eb0cfda).
4. Integration: MCP client → uvicorn → testcontainers Mongo, `test-int` target (7e14e67).
5. Gates and 16/16 mutation checks (74c0d5d).

## Next
Code + security review → fix → handoff → goal-verifier → board `in review` (hold for Haneef).

## Surprises
- A module-level OTel tracer stays bound to the first provider; the tracer is looked up per call.
- FastMCP's generated arg models ignore unknown args; the contracts subclass `ArgModelBase` with
  `extra="forbid"` and are installed through `FuncMetadata`.
- The session's worktree guard refuses compound shell commands that write files; use Edit/Write.
