# Checkpoint — MCP-2 · goal/mcp-2-query-validator

**Done**
1. Tests first (corpus of 82 payloads, properties, server, tool surface). RED in `docs/evidence/MCP-2/red-step1*.txt`.
2. `validator.py` + `validator_rules.py`, one call in `server.py`.
3. Code and security reviews: no critical or high findings; fixes in 2019078.
4. Mutation checks: 27 of 27 caught (`mutation-checks.txt`); property-only run 8 of 10 (`mutation-checks-properties.txt`).
5. Gates: `make check` and `make test-int` green (`docs/evidence/MCP-2/`).
6. Handoff written (attempt 1).

**Next:** goal-verifier, then set the board to `in review` (hold for the orchestrator; no merge).

**Surprises:** see the handoff's "Traps hit".
