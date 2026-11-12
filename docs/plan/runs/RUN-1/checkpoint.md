# Checkpoint — RUN-1 · goal/run-1-orchestrator-core

## Done
Everything below is committed.
- **Build:** run domain (`onb_agents.runs`); orchestrator (checkpointer, graph, runner, consumer, worker); `POST /runs`; cross-tenant cases; `apps/orchestrator/tests_int`; ADR 0015.
- **Review fixes:** two independent reviews; every critical, high and medium finding fixed except HTTP idempotency of `POST /runs`, which is left for RUN-2/SEC-2. Details are in the handoff.
- **Mutations:** 21 of 21 killed (`docs/evidence/RUN-1/mutation-checks.txt`).
- **Gates, all green:** `make check` (test-xt `206 passed`), `make test-int` (9 projects), `make contracts-check`, `make evals-selftest`.
- **Handoff:** `docs/plan/runs/RUN-1/handoff.md` (attempt 1).

## Next
1. goal-verifier (attempt 1).
2. On pass: set the board to `in review` and run `make down`. Hold for the orchestrator; no merge or rebase.

## Verifier
- attempt 1 (HEAD f674d92): **pass**, all 5 boxes met, gates pass. Notes fixed after: the type-ignore reason, the probe count in the handoff (86/20000 as recorded).
- Left on the ob-run-1 Mongo volume: one onb_eval_* scratch database from a crashed eval self-test run (not dropped; `make evals-selftest` cleanup asserts until it is).
