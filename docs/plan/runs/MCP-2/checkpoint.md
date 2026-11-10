# Checkpoint — MCP-2 · goal/mcp-2-query-validator

**Done**
1. Tests first: `tests/corpus.py` (75 payloads), `test_validator.py`, `test_validator_properties.py`
   (hypothesis), `test_server_validator.py`, `test_tool_surface.py`. RED in
   `docs/evidence/MCP-2/red-step1*.txt`.
2. `src/onb_mcp_mongo/validator.py` (new module) + one call in `server.py:_run_traced`. 408 unit
   tests pass; ruff and mypy clean.
3. Mutation checks: `docs/evidence/MCP-2/mutation-checks.txt`, 20 of 20 caught.

**Next:** code + security review, `make check`, `make test-int`, handoff, goal-verifier.

**Surprises**
- The MCP SDK client cannot serialise very deep JSON (pydantic depth), and the server refuses
  JSON-RPC params past ~300 levels itself (-32602) before any tool runs.
- MCP-1's `test_a_malformed_update_operator_is_denied_not_an_internal_error` now gets
  `invalid_update` from the validator rather than `invalid_query` from the data layer.
