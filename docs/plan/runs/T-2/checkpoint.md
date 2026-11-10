# Checkpoint — T-2 · Catch any unscoped query at runtime

**Branch:** goal/t-2-query-safety-net · **Status:** all three boxes built and proved; handoff written; waiting for the verifier.

## Steps done
1. Listener `onb_data.safety_net` with unit tests (RED, then GREEN, 100% coverage).
2. Integration guard: install in `pytest_configure`, autouse `tenant_safety_net_guard`, fixture tests, leaked/raw client tests. T-1/F-2 root read-backs made explicit.
3. Index check across the server, plus seen-once evidence.
4. Review fixes (update bodies, change streams, explain, views, session-end check).
5. 12 mutation checks, all caught. Gates recorded.

## Next step
Run the goal-verifier, then set the board to `in review` (hold for the orchestrator; no merge).

## Surprises
- PyMongo swallows listener exceptions.
- `make check` is red on main's `py-guardrails` property test (`redact("_account number 000000000")`), outside T-2.
