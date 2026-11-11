# Checkpoint — RUN-1 · goal/run-1-orchestrator-core

Stopped early: the session hit its usage limit. The board shows RUN-1 `claimed`.

## Done (committed)
- `libs/py-agents/src/onb_agents/runs/`: the task and run state machines (FR-R2/R3), run and task records (the hash is recomputed before persisting; `stored_dag` checks it again on read), `RunStore` (compare-and-set moves), `RunStarter` (publishes through the outbox with `change=` and the key `run:<id>:started`, audits `run.start`, stores the traceparent), and `run_bus` / `SUBSCRIPTIONS`. `testing.py` holds the diamond and uneven templates and `OutboxStandIn`.
- `apps/orchestrator`: `TenantCheckpointSaver` (a tenant-repo Mongo checkpointer that refuses any thread other than `org_id:run_id`), `build_run_graph` (one node per task, joins via `add_edge([deps], t)`), `RunExecutor` + `start_run_handler` (idempotent, resumes from the checkpoint, drives only started/running runs), `StubExecutor`, and the worker (`python -m onb_orchestrator`). Unit tests cover boxes 1–4: `apps/orchestrator/tests/test_run_graph.py`, `test_checkpointer.py`.
- API: `POST /runs` (HR, HR_ADMIN) via `RunService`; `create_app(runs=)` is added at the end of each list; `main.py` is wired; there is a role-matrix row; contracts are regenerated (`onb_contracts.runs`). Tests: `apps/api/tests/test_runs_route.py` (including pinning after v2 is published).
- test-xt (uncommitted at the time of writing, committed with this checkpoint): the `POST /runs` ROUTE_CASE; PLATFORM_SUBSCRIPTIONS and BUS_MODULES (`onb_agents/runs/events.py`); a CONSUMER_CASES row for `orchestrator.start_run`. The seeded runs come from `world.xt_run_documents` inside `tenant_documents`.
- Last `make test-xt`: **201 passed, 3 errors**. All 3 errors were `ServerSelectionTimeoutError` (tunnel connection closed) on the GET /hires generic cases; none was an assertion failure. Re-run to confirm the errors were transient.

## Next steps
1. Re-run `make test-xt`. If tunnel errors keep coming back, check Docker load from the other ob-* stacks.
2. Integration tests on compose Mongo in `apps/orchestrator/tests_int/` (add a `test-int` target to the project.json, and a conftest copying the api tunnel plus the T-2 safety net): POST /runs → real bus → worker → graph, covering diamond overlap, thread_id in `run_checkpoints`, pinning, and a rolled-back publish leaving nothing.
3. ADR `docs/adr/0015-tenant-checkpointer.md`: the PRD names `langgraph-checkpoint-mongodb`, which conflicts with §7.9 (TenantRepository, org_id on checkpoints), so a custom saver is used. Report this PRD conflict in the handoff.
4. Mutation checks: join → separate edges (`test_a_join_waits_for_every_branch_even_a_longer_one`); `dependencies_met` treating skipped as blocking; the transition table; the thread_id org check; the hash recompute; role guard on /runs.
5. code-reviewer and security-reviewer; `make check`, `make test-int`, `make contracts-check`, `make evals-selftest` (py-agents changed); then the handoff, the goal-verifier, the board set to `in review`, and `make down`.
