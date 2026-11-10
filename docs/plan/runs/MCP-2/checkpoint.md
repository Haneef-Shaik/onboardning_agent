# Checkpoint — MCP-2 · goal/mcp-2-query-validator · ON HOLD after verifier attempt 4

**State (473e4e1)**
- Boxes 1–3 pass in all four verifier runs. `make check` is green (mcp-mongo 509 passed) and `make test-int` is green (7 projects).
- Box 4 is now proved behaviourally, per the orchestrator's decision. `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` lists tools over a real MCP session against the exact app `main()` hands to uvicorn. It kills the attempt-3 bypass (V35). Mutations: 35 of 35 caught.
- **Attempt 4 fail (box 4), with two new survivors:**
  - **M1:** `main.py` mounts a second, aliased FastMCP at `/ops` (chained lifespan) that serves `db_users.run_command`. The test only connects to `/mcp`.
  - **M2:** `main.py` adds a tool only when `env is None`, which is the production call. The test always calls `main(dict)`.

**Next step if resumed (the verifier's retry guidance; small, test-only)**
1. Call `main()` with no argument, as production does, with the `MCP_*` variables set by `monkeypatch.setenv`.
2. Walk the served app's route table recursively (every `Mount`, `Host` and `Router`; also `user_middleware`). Assert it is exactly one streamable-HTTP route at `/mcp`. Then list tools on `/mcp` as now.
3. Add M1 and M2 to `mutate.py`, show both caught, and refresh `mutation-checks.txt`, `make check` and the handoff (attempt 5).

**For the integrator:** each attempt has closed every survivor found, and the verifier then finds a new edit to `main.py` aimed at the test. A test cannot rule out every hostile rewrite of the entry point, so the bar for box 4 needs a stated threat model. A reasonable one:
- the served app's whole route table is fixed;
- tools are listed over MCP on its only endpoint;
- the production call path is used;
- the static scan stays as a second layer.
