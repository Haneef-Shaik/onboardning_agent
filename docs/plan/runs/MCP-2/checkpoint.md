# Checkpoint — MCP-2 · goal/mcp-2-query-validator · ON HOLD after verifier attempt 5

**State (6a58aa8)**
- Boxes 1–3 pass in all five verifier runs. `make check` green (mcp-mongo 510 passed); `make test-int` green (7 projects).
- Box 4 follows the confirmed threat model: `main()` runs bare on its production path; the served app's whole route table must be exactly `/mcp`; tools are listed over a real MCP session. Mutations: 37 of 37 caught (V35–V37 are the attempt-3 and attempt-4 survivors).
- **Attempt 5 fail (box 4, in scope).** The production-path proof runs only with the default configuration, because `_served_app` removes `MCP_MONGO_HOST`/`PORT`/`DATABASE`. Three survivors:
  - **P1:** an aliased FastMCP plus `Tool.from_function(db_users.run_command)` behind `if settings.host != "127.0.0.1"`.
  - **P2:** the same, behind `settings.port != 8000`.
  - **P3:** the same, behind an ad-hoc env flag (`MCP_MONGO_OPS_TOOLS`).

  The static layer misses them as well: the AST rule matches local names only, and `run_command` lives in the exempt `db_users.py`.

**Next step if resumed (small, tests only)**
1. Parametrize `_served_app` over (a) required vars only and (b) every optional setting at a non-default, production-like value (`MCP_MONGO_HOST=0.0.0.0`, `MCP_MONGO_PORT=9001`, `MCP_MONGO_DATABASE=onb_x`). Run the route-table test and the `list_tools` test for each. This kills P1 and P2.
2. Close P3 statically, since a test cannot enumerate unknown flags:
   - outside `server.py`, no import from `mcp.server`, `mcp.server.fastmcp*` or `mcp.server.lowlevel` under any alias (check `ImportFrom.module` and `Import` names);
   - `main.py` may not read `os.environ` or `getenv` except to pass it to `load_settings`.
3. Add P1–P3 as mutations V38–V40, show each killed, and refresh `mutation-checks.txt`, `make check`, `make test-int` and the handoff (attempt 6).
