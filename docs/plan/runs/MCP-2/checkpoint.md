# Checkpoint — MCP-2 · goal/mcp-2-query-validator · ON HOLD after verifier attempt 7 (the declared last attempt)

**State (c0393ea)**
- Boxes 1–3 pass in all seven verifier runs. `make check` green (mcp-mongo 518 passed); `make test-int` green (7 projects). Mutations: 41 of 41 caught, including V41 (attempt-6 survivor A).
- Box 4 implements the orchestrator's FINAL narrowed model:
  1. production-path `main()` serves one MCP endpoint with exactly the six PRD tools (two configurations);
  2. only `server.py` imports `mcp.server`, and `FastMCP` is constructed exactly once, inside `build_server`;
  3. nothing starts a server or listener except `main()`'s single `uvicorn.run`.

  Survivor B (configuration combinations) is out of model and flagged for SEC-2 in the handoff.
- **Attempt 7 fail (box 4).** The verifier classed all four survivors IN MODEL; each passes all 518 tests:
  - **Q1 (guarantee 2):** `server.py` does `import mcp.server.fastmcp as fastmcp_mod` and builds a module-level `OPS = fastmcp_mod.FastMCP("ops", tools=[... drop_collection ...])`. The exactly-once test resolves only from-imported names (`ast.Name`), not attribute chains.
  - **Q3 (guarantee 2):** `class _Ops(FastMCP): pass; OPS = _Ops(...)`. Subclass construction is not counted.
  - **Q4 (guarantee 3):** `main.py` calls `os.system("npx -y mongodb-mcp-server")` before `uvicorn.run`. The listener rule forbids `subprocess`/`multiprocessing` but not the `os` spawn family (`os.system`, `popen`, `spawn*`, `posix_spawn*`, `exec*`, `fork`) or `pty`.
  - **Q2 (guarantees 2 and 3, end to end):** Q1's server becomes a module-level `ops_app` in `server.py`, and `main.py` runs `os.system("uvicorn onb_mcp_mongo.server:ops_app --port …+1")`. The verifier confirmed over HTTP that it lists `drop_collection`.

**If the integrator allows another round (the verifier's guidance)**
1. **Exactly-once test:** resolve `FastMCP` through attribute chains on any `mcp…` import alias as well as from-imports. Fail on any `ClassDef` with a `FastMCP` base.
2. **Listener rule:** forbid the `os` process-spawn family and `pty`, ideally by forbidding every `os.<attr>` call except an allowlist (`main.py`'s `os.environ` passed to `load_settings`; `db_users.py`'s environment read).
3. **Mutations:** add Q1–Q4 as V42–V45, show each killed, refresh the evidence and the handoff.

This is the same pattern as attempts 2–6: each closure is followed by a new ordinary-code spelling. A different framing of box 4 may be needed rather than another rule.
