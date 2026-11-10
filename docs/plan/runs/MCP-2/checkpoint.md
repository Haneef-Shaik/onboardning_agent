# Checkpoint — MCP-2 · goal/mcp-2-query-validator · ON HOLD after verifier attempt 6

**State (fafe580)**
- Boxes 1–3 pass in all six verifier runs. `make check` green (mcp-mongo 516 passed); `make test-int` green (7 projects). Mutations: 40 of 40 caught, including V38–V40 (the attempt-5 survivors).
- **Box 4** follows the confirmed threat model plus the approved attempt-6 plan:
  - production-path `main()` in two configurations (required-only, all-non-default);
  - the served app's route table is exactly `/mcp`;
  - tools are listed over a real MCP session;
  - static rules: `mcp.server` is imported only by `server.py`, and the environment is read only via `load_settings`.
- **Attempt 6 fail (box 4).** The verifier classed both survivors as IN MODEL. Each passes all 516 tests:
  - **A, a second endpoint through `FastMCP.run`:** `server.py` gains `build_ops_server(fn, host, port)` returning `FastMCP(..., tools=[Tool.from_function(fn, name="run_command")])`. `main.py` starts it with `threading.Thread(target=ops.run, args=("streamable-http",), daemon=True).start()` on port+1, before the normal `uvicorn.run`. The test patches only `uvicorn.run`, so a server started any other way is never captured.
  - **B, a branch on a combination of settings:** `build_server` appends a `drop_collection` tool when `host != "127.0.0.1" and port == 8000` (a networked host on the default port). Only two configuration points are tested, so mixed combinations go unexercised.

**Next step if resumed (attempt 7, needs integrator approval; the verifier's retry guidance)**
1. **A:** prove `main()` starts no listener other than the app it passes to `uvicorn.run`. Two options:
   - patch or forbid `FastMCP.run*`, `uvicorn.Server`/`Config`, `anyio.run`, `threading.Thread.start` and `asyncio` loop starts during `main()`;
   - add a static rule: `main.py`/`server.py` never call `.run*`, `uvicorn.Server` or `uvicorn.Config`, and `FastMCP(` is constructed exactly once, inside `build_server`.

   Mutation V41.
2. **B:** cover configuration combinations. Two options:
   - run all 8 combinations of {default, non-default} for HOST, PORT and DATABASE;
   - prove statically that `main`/`build_server` use host, port and database only as pass-through arguments (never compared or branched on).

   Mutation V42.
