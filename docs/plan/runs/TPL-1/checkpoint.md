# Checkpoint — TPL-1 · goal/tpl-1-template-validator

## Done
1. `when` parser, `onb_agents.templates.expr` (69c863c). RED: `docs/evidence/TPL-1/red-when-expr.txt`.
2. Template schema, `onb_contracts.templates` (189fe42). RED: `red-template-contracts.txt`.
3. FR-T3 validator (`validator.py`, `graph.py`, `sensitive.py`) and `templates/engineer-india.yaml` (d5e69b5).
   RED: `red-template-validator.txt`.
4. EVAL-1 interim template contract replaced by the real schema (171bffb). RED: `red-eval-contract.txt`.
5. `make check` passed (`docs/evidence/TPL-1/make-check.txt`).

## Next
- Integration gates (`make test-int`, `make evals-selftest`, `make contracts-check`): output in `docs/evidence/TPL-1/`.
- Code and security reviews: fix critical and high findings.
- Mutation checks: `uv run --frozen python docs/evidence/TPL-1/mutate.py > docs/evidence/TPL-1/mutation-checks.txt`.
  Run it only when nothing else reads the source, because it edits files in place.
- Handoff, then goal-verifier. Hold at `in review` (orchestrator integrates). Then `make down`.

## Surprises
- The `rtk` hook truncates `make` output. Use `rtk proxy make …` to keep the full result lines.
- The worktree guard refuses compound shell commands that mix heredocs and git. Write files with the Write
  tool, then run the commands separately.
