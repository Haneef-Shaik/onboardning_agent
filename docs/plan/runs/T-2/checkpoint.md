# Checkpoint — T-2 · Catch any unscoped query at runtime

**Branch:** goal/t-2-query-safety-net · **Status:** attempt 3. All boxes proved, gates green at d2a5917, handoff updated; the goal-verifier is next. Hold at `in review` (no rebase, merge or push).

## Steps done
1. Listener `onb_data.safety_net` with unit tests (RED, then GREEN).
2. Integration guard, leaked/raw client tests, index check with seen-once evidence.
3. Review fixes (update bodies, change streams, explain, views, session-end check).
4. Attempt 2: rebased on main 707b60c (EVT-1, PII-3). Worker-queue allowance by shape (ADR 0002). The eventbus suite is under the guard.
5. Attempt 3 (verifier attempt 2 failed box 2): shared plugin `onb_data.safety_net_pytest`. py-crypto and py-telemetry suites are guarded. `tools/tests/test_safety_net_suites.py` fails `make check` on any unguarded Mongo suite.
6. 25 mutation checks, all caught. `make check` and `make test-int` exit 0.

## Next step
Run goal-verifier attempt 3. On pass: board `in review` (ref branch @ HEAD), activity entry, `make down`.

## Surprises
- PyMongo swallows listener exceptions.
- The verifier found PII-1's py-crypto Mongo suite outside the net (attempt 2).
- nx forces colour into subprocess pytest sessions.
- The machine restarted mid-session; the worktree survived intact.
