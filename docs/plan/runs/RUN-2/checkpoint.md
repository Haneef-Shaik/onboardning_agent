# Checkpoint — RUN-2 · Survive restarts and let HR control a run

Branch `goal/run-2-run-control` (from main fcc6ab9). Stack `ob-run-2` (compose project, Caddy 19400/19401).

## Steps done (all committed, tests first)
1. States: run `paused`/`blocked`/`cancelled`, task `failed → ready|done`; contracts statuses.
2. Store: task leases (`take_over`, `renew_lease`, `release_lease`), `history` on tasks and runs, `list_runs`,
   token compare-and-set on running-task moves.
3. Event bus: `ReleaseJob` (hand a job back, no attempt used).
4. Engine: retry with backoff, takeover, block + `run.blocked` in one transaction, pause/cancel halt,
   drain, checkpoint deletion, whole-task checkpoint writes (found by the SIGKILL test), worker consumers
   and env policy.
5. API: `GET /runs`, `GET /runs/{id}`, pause/resume/cancel, task retry/complete (reason, audit), manager
   scope, `GET /runs/{id}/stream` (SSE); contracts regenerated.
6. Cross-tenant cases for the 8 routes and the 2 new consumers; role-matrix rows.
7. Integration on the compose replica set: SIGKILL + restart (both stages), block + resume through the bus.

## Next
- Done: mutation checks 20/20, reviews, gates, handoff, verifier pass (attempt 1). Status: in review, held for Haneef.

## Surprises
- The eval seed fixture keeps its own-shaped `onboarding_runs` in the same collection: the run routes ignore
  documents without a `dag_hash`.
- LangGraph took a half-written task (killed between two checkpoint-write documents) as complete: a third
  of the SIGKILL runs ended `failed: IncompleteRun`. Fixed in the saver (`count` per task).
- `resume` worked on a never-paused run because `started → running` is a legal engine move: now paused only.
