# Checkpoint — AG-1 · goal/ag-1-agent-runtime

## Steps done
1. Dependencies: langgraph, langchain-mcp-adapters, langchain-core, mcp in py-agents (uv.lock).
2. Output contracts `onb_contracts.agent_outputs` + schema tests (box 4); evals use the shared payload models.
3. Local tools `date_calc`, `render`.
4. Per-task token scopes, grant header (`runtime/task.py`).
5. Runtime: `runtime/{tools,trajectory,prompt,graph,runner}.py`; box 1-3 tests against the real hub in process.
6. Mutation checks 32/32 (`docs/evidence/AG-1/mutation-checks.txt`); ADR 0010.

## Next step
Code and security review of `git diff main...HEAD`, fix findings, then gates (`make check`,
`make test-int`, `make evals-selftest`), handoff, verifier, board `in review`.

## Surprises
- The fake connector refuses scripted calls to unbound tools, so box 1's fake-connector test uses a
  runtime manifest wider than the registry's; a second test uses a model that names unbound tools.
- py-agents tests import `onb_mcp_hub` (test-only; the hub depends on py-agents, not the other way).
