# Checkpoint — UI-8 · Admin views

Branch `goal/ui-8-admin-ui` (from main 5f9d3e5). Stack: `ob-ui-8` (Caddy 19500/19501). Playwright ports
19531/19532 for runs, 19541/19542 for the mutation script.

## Done
1. API: `GET /evals/runs`, `GET /evals/runs/{run_id}` (HR_ADMIN, tenant-scoped, paged); `resolved_llm`
   on agent records (`create_app(llm=)`, main.py loads config/llm.yaml). Role matrix rows; 386 API tests.
2. test-xt: eval runs seeded per org (`world.EVAL_RUNS`), ROUTE_CASES for both routes; `make test-xt`
   147 passed. Contracts regenerated.
3. Web: `/admin/*` (Agents, agent page with recent denials, Audit log, PII access log, Eval runs + run).
   Vitest 70 passed.
4. Playwright `apps/web/e2e/admin.spec.ts` + `admin_seed.py`: 9 passed (4 new).
5. ADR 0014.

## Next
- `make check` green; mutation script `docs/evidence/UI-8/mutate.py`; fix review findings; handoff;
  ACCEPTANCE U8 row; goal-verifier; board → in review; `make down`.

## Surprises
- The shell guard rejects long heredocs; edit scripts live in the job tmp dir.
- main.py now needs `LLM_MODEL_*` at startup; test_telemetry/test_auth_settings set a fake LLM env.
