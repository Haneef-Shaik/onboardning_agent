# 0018 · How a run survives a restart, retries, blocks, and is controlled by HR

- **Status:** accepted · 2026-11-12 · goal RUN-2
- **Context:** FR-R4 says failed tasks retry with backoff and, after the maximum attempts, the run is
  `blocked` and HR is notified. FR-R5 says HR can pause, resume and cancel a run and retry or manually
  complete a task, with an audited reason. FR-R6 says runs survive process restarts. ADR 0015 left RUN-2
  these: a task left `running` by a crashed worker is never taken over, a worker has no graceful stop,
  checkpoints are never deleted, and `POST /runs` is not idempotent over HTTP. NOTIF-1 already defined
  the `run.blocked` event and the in-app consumer that tells HR; RUN-2 is its producer.

## Decision

1. **A running task has a lease, and a task whose lease ran out is taken over.** Moving a task to
   `running` writes `lease_token` and `lease_until`; the executing delivery renews it every third of
   `lease_seconds` (default 30 s). A task found `running` with a live lease belongs to another delivery
   (transient `TaskInProgressError`); with an expired or released lease its holder is gone, and the
   delivery takes it over with a compare-and-set on the dead token (`RunStore.take_over`). Finishing or
   failing a running task is also compare-and-set on the token, so a worker that was taken over cannot
   overwrite the new holder's result. The bus job's lease follows the same `lease_seconds`, so a killed
   worker's job and its task become claimable together.
2. **Exactly-once effects are at-least-once execution plus an idempotent effect.** A takeover runs the
   task again, so a task killed after its effect and before `done` runs twice. Every attempt of a task
   carries the same `idempotency_key` (`<run_id>:<task_id>`, `TaskWork`); an executor or tool must apply
   its effect at most once per key. **RUN-3 and the MCP hub must pass it to every effectful tool.** The
   SIGKILL test proves the contract with a sink that dedupes on the key.
3. **A takeover counts as an attempt**, so a task that keeps killing its workers reaches
   `max_attempts` (default 3) and blocks the run (`error = LeaseExpired`) instead of being taken over
   for ever. A task cut off through no fault of its own (a sibling failed, the worker is stopping)
   releases its lease and gives the attempt back.
4. **Retry, then block.** A failed attempt leaves the task `failed` with the error type and `retry_at`
   (backoff 2 s, 4 s ... capped at 60 s, stored so a restart honours it), then `failed → ready →
   running`. At the last attempt the run moves `running → blocked` and `run.blocked {run_id, task_id}`
   is published **in the same transaction** (key `run:<id>:blocked:<history length>`), so HR is told
   exactly once and never zero times. The run bus subscribes `run.blocked` to NOTIF-1's `notify.in_app`.
   The other tasks wait untouched; a blocked run keeps its checkpoint. This replaces RUN-1's rule that a
   failed task fails the run and cancels the rest; the run still `failed`s for deterministic errors
   (a changed DAG, a broken graph).
5. **Controls.** `pause` (started or running → paused) lets the task in hand finish and starts no new
   one; `resume` (only from paused) and a retry or hand-completion that clears the last failed task of
   a blocked run publish `run.resumed`, and the orchestrator drives the run on from its checkpoint;
   `cancel` cancels every unfinished task and publishes `run.cancelled`. `retry` resets the task's
   attempts to 0 (a fresh budget); `complete` marks a failed task done by hand. A blocked run stays
   blocked until every failed task has been retried or completed, so HR is not told twice for one run.
   Each control writes the state change, its audit record (with the reason) and its event in one outbox
   transaction. `complete` requires a reason (422 without); the reason is kept in the audit log only.
6. **The stream replays history.** Each task and run keeps a `history` of its moves (`from`, `to`, `at`,
   `by`), appended in the same write as the move. `GET /runs/{id}/stream` sends the entries it has not
   yet sent, by polling (1 s), and ends when the run is finished or after an hour (the client
   reconnects and replays). No transition can be missed or repeated however fast the orchestrator
   moves; a Mongo change stream would add a replica-set dependency for no gain at this volume.
7. **Graceful drain.** SIGTERM sets a drain flag: the task in hand finishes, no new one starts, and the
   handler raises `onb_eventbus.ReleaseJob`, which hands the job back to the queue at once with its
   attempt returned. A rolling restart therefore never uses up a job's attempts however long the run.
   A task that outlasts `ORCHESTRATOR_DRAIN_SECONDS` is cancelled and releases its lease.
8. **Checkpoint retention.** A run that completes or fails deletes its checkpoints
   (`TenantCheckpointSaver.adelete_thread`); `run.cancelled` has a consumer that deletes a cancelled
   run's. Blocked and paused runs keep theirs. A task's checkpoint writes count only when **all** of
   them are stored (each carries the task's write count): a process killed between two of them left the
   task half recorded, LangGraph took the first write as its whole output, and the tasks it should have
   triggered never ran (the SIGKILL test found this: the run ended `failed: IncompleteRun` in about a
   third of the kills). The run also now refuses to `complete` with an unfinished task.
9. **Not built: HTTP idempotency of `POST /runs`.** RUN-1 left it open (a client retry starts a second
   run). Two fixes fit, and the choice is a product one, so it is left for Haneef (and SEC-2, with rate
   limiting): an `Idempotency-Key` header with a per-org key table (safe retries, any number of runs per
   hire), or a partial unique index on active runs per hire (`started`, `running`, `paused`,
   `blocked`; one onboarding at a time, which would also stop a second run being started by mistake).
   RUN-2's `active` property on `RunState` is the set the index would use.

## Consequences

- New run states `paused`, `blocked`, `cancelled`; new task moves `failed → ready` and `failed → done`.
  `contracts-check` and the generated TS client carry them.
- The run bus's subscriptions grew by three (`run.resumed`, `run.cancelled`, `run.blocked`); the
  orchestrator worker runs three consumers. The cross-tenant suite has a case for each route and consumer.
- Executors must be idempotent on `idempotency_key` (decision 2). RUN-3 and HUB-1 inherit that.
- The eval fixture's own `onboarding_runs` documents (another shape) share the collection in test
  databases; the run list and read routes ignore documents that are not runs of this platform.
- The audit `reason` is free text. It must not carry personal data; enforcing that is GR-1/SEC-1's.

## Addendum — reconciled with RUN-3's agent tasks (attempt 2)

RUN-3 (ADR 0019) merged first, so RUN-2 was rebased onto it and these were decided:

10. **A sink-rejected task is an ordinary failed attempt.** `orchestrator.fail_task` still fails the
    `running` task at once (now also releasing its lease and appending to its history, so the stream shows
    it). The driver that was running the task sees the compare-and-set conflict on its own move, never
    turns the task `done` (RUN-3's rule, kept), and treats it as that attempt's failure: the task is
    retried with backoff while attempts remain, and at `max_attempts` the run is **blocked** (not failed)
    and HR is notified. The cause (`failure = {kind: sink_rejected, reason}`, `error = sink_rejected`)
    stays on the task. RUN-3's "the run fails when its driver sees the failed task" is superseded by FR-R4.
11. **A retry starts clean.** Moving a task to `running` clears its `error`, `retry_at` and `failure`, so a
    retried task never shows the previous attempt's cause (RUN-3's open item). A manual retry clears them too.
12. **Recovery of a run whose driver died after `fail_task`.** The task is `failed` with the sink's cause and
    the run `running`. The driver's job lease runs out and the job is redelivered; the next delivery finds the
    failed task and retries it (or blocks the run at its maximum). Nothing else is needed while the job is
    redelivered; a dead-lettered job is the open item of this ADR's review list (resume republishes one).
13. **Executor errors are classified by the executor.** An error may say `retryable = False` (RUN-3's
    `AgentTaskFailedError` does for `no_agent`, `retired_agent`, `grant_refused`, `budget_exceeded`,
    `guardrail_blocked`): the task fails once and the run is blocked at once, since identical retries cannot
    help and (for a budget) would spend more. Every other error, and a sink rejection, is a normal attempt.
    A database error from the executor (RUN-3 re-raises them) is not the task's failure: the lease is
    released, the attempt handed back and the job retried. The failure `kind` an executor reports is the
    task's `error` for HR.
14. **Span nesting.** The executor is started inside the task span, so the agent, its LLM and tool calls are
    children of `orchestrator.task` (run → task → agent → LLM/tool, RUN-3's tree test).
15. **Exact subscription lists** now name every consumer: `start_run`, `resume_run`, `cleanup_run`,
    `fail_task` on the orchestrator's side (`notify.in_app` on the run bus), and `task.sink_rejected` stays in
    both `run_bus` and `NOTIFY_SUBSCRIPTIONS`.
