# RUN-3 checkpoint

Branch goal/run-3-agents-in-runs (from main fcc6ab9). ADR 0019 reserved.

## Done
1. AgentTaskExecutor (apps/orchestrator/src/onb_orchestrator/agent_executor.py): agent lookup, grant only
   for `pii_access` (run's own run_id), model recorded once on the task, per-run token budget, outcome on task.
   Boxes 1-3 tests: apps/orchestrator/tests/test_agent_executor.py (green).
   RunStore additions: record_model, record_outcome, run_tokens.

## Next
2. Spans (box 4): orchestrator.run → orchestrator.task → agent.run → llm / tool.call.
3. task.sink_rejected consumer (fail the task) in run_bus SUBSCRIPTIONS + xt CONSUMER_CASES.
4. Worker wiring (settings), ADR 0019, mutation checks, review, gates, handoff.
