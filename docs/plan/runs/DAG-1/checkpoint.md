# Checkpoint — DAG-1 · branch goal/dag-1-dag-compiler

## Done
- Tests first (`5b3ced2`, RED in `docs/evidence/DAG-1/red-dag-compiler.txt`).
- `onb_agents.dag`: `compile_dag`, `CompiledDag`, `CompiledTask`, `CompileError`, `CompileReason`
  (`d76a5e7`). All 4 boxes covered by `libs/py-agents/tests/test_dag_compiler.py` and
  `test_dag_properties.py`; 100% coverage of `onb_agents/dag`.
- Mutation checks 30/30 killed (`docs/evidence/DAG-1/mutation-checks.txt`, `1f724ac`).
- ADR 0008 (`docs/adr/0008-dag-compilation.md`).

## Next
- code-reviewer on `git diff main...HEAD`; fix critical/high.
- Gates: `make check`, `make evals-selftest` (diff touches libs/py-agents); then `make down`.
- Handoff, goal-verifier, board → in review (hold for orchestrator; no merge).

## Surprises
- Python 3.12 `date.fromisoformat` accepts `20261201` and `2026-W48-2`; the compiler requires `YYYY-MM-DD`.
- The worktree guard refuses commands whose text contains "git" inside words (e.g. "digits"); use helper scripts.
