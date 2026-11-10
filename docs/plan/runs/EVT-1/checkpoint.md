# Checkpoint — EVT-1 · Move work through a durable Mongo event bus

Branch `goal/evt-1-event-bus`. Stack: `COMPOSE_PROJECT_NAME=ob-evt-1`, Caddy 18800/18801 (`.env`, git-ignored).

## Steps done
1. `onb_data`: `DataStore.run_transaction` (session in a contextvar, passed by every repository call),
   `DataStore.queue("jobs" | "dead_letters")` → `QueueRepository` (claim, leased update/delete,
   dead-letter insert, bounded find; updates never touch `org_id`). Unit + int tests. (22e741e)
2. `onb_eventbus`: `EventBus` interface, `MongoEventBus.publish` (outbox, per-org idempotency),
   `MongoWorker` (lease claim, heartbeat, backoff, dead letters, tenant context, trace). 70 unit
   tests over an in-memory Mongo double. (599ab66)
3. Integration tests for boxes 1–5 and the consumer cross-tenant case against compose Mongo;
   `py-eventbus:test-int` target. (3fab1fd)

4. Mutation checks (18/18 caught), ADR 0002, code + security review fixes (cf5d372), gates green
   (`make check`, `make test-int`), evidence in `docs/evidence/EVT-1/`, handoff written.

## Next
goal-verifier (attempt 1) → board `in review` → `make down`. Orchestrator integrates; don't rebase/merge.

## Surprises
- The OTel ProxyTracer caches the real tracer: a module-level `get_tracer` breaks after the test
  fixture resets the provider. Tracers are fetched per call.
- `make test-xt` does not exist yet (T-3 not started); the consumer cross-tenant case lives in
  `libs/py-eventbus/tests_int` and `bus.consumers()` is the extension point for T-3.
