# Checkpoint — MCP-2 · goal/mcp-2-query-validator · ON HOLD after 3 verifier attempts

**State (e66a6ad, rebased onto main with MCP-3 and EVAL-1 by the orchestrator)**
- Boxes 1–3 pass in all three verifier runs (82-payload corpus, 9 hypothesis properties, the pii/org_id tests).
- Attempt 1: pass, before the rebase.
- Attempts 2 and 3: **fail on box 4 only**. After the rebase, MCP-3's `db_users.py` legitimately calls `.command()`. Exempting it from the "no destructive call in source" scan opened a gap: a tool can be served whose body lives in the exempt module.
  - Attempt 2 survivors: `main.py` registering a tool with `add_tool`; a nested `db_users.py` look-alike file. Both fixed in 90ec295.
  - Attempt 3 survivor: `main.py` builds an **aliased** `FastMCP` (`FastMCP as Hub`) with `tools=[..., Tool.from_function(db_users.run_command)]` and serves that app. `test_the_server_main_serves_has_exactly_the_prd_tools` lists `build_server`'s tools, not those of the app handed to uvicorn. The AST rule matches local names only.
- `make check` green (mcp-mongo 509 passed); `make test-int` green (7 projects) at 90ec295/e66a6ad.

**Next step if resumed (the verifier's retry guidance)**
1. In the `main()` test, enumerate tools over a real MCP client session against the app actually passed to `uvicorn.run`, not `built[0]`.
2. Make the "only server.py builds or registers" rule resolve imports: flag any import of `mcp.server.fastmcp`, `mcp.server.lowlevel`, `FastMCP`, `Server` or `Tool` (any alias) outside `server.py`, plus `tools=` kwargs, `from_function`, `_tool_manager`, and `list_tools`/`call_tool` decorators.
3. Add the attempt-3 bypass as mutation V35; refresh `mutation-checks.txt`, `make check`, `make test-int` and the handoff (attempt 4).

**Recommendation for Haneef:** see the session report. Static checks on source alone keep losing to new bypasses. The durable fix is structural: either move `db_users.py` out of the `onb_mcp_mongo` app package (into a separate provisioning package or CLI that the server package cannot import), or assert on what is actually served over MCP from `main()`.
