# Checkpoint — UI-8 · Admin views

Branch `goal/ui-8-admin-ui` (from main 5f9d3e5). Status: **in review** after goal-verifier pass
(attempt 1). Held for the orchestrator: no rebase, no merge.

## Done
1. API: `GET /evals/runs`, `GET /evals/runs/{run_id}`; `resolved_llm` on agent records; role matrix rows.
2. test-xt ROUTE_CASES for both eval routes + harness-compat tests (`test_xt_eval_records.py`).
3. Web `/admin/*` views; Vitest 70 passed.
4. Playwright 9 passed, direct and via Caddy; U8 ledger row written.
5. ADR 0014; mutations 13/13; make check, test-xt, test-int, contracts-check green.
6. Handoff `docs/plan/runs/UI-8/handoff.md`; verifier verdict pass.

## Next
- Orchestrator: rebase, re-run gates, ff-merge, republish the board page, mark done.
