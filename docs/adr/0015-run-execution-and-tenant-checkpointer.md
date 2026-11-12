# 0015 · How a run starts and executes, and where its checkpoints live

- **Status:** accepted · 2026-11-12 · goal RUN-1
- **Context:** PRD §7.1 says a published template version and the hire's variables compile into a
  LangGraph `StateGraph` (one node per task, edges from `depends_on`, parallel fan-out and fan-in),
  made durable with `langgraph-checkpoint-mongodb` on `thread_id = "{org_id}:{run_id}"`. §7.9 says all
  database access goes through `TenantRepository`, every tenant document carries `org_id`, the T-2
  safety net fails any command on a tenant collection without `org_id`, and "checkpoints carry
  `org_id`". FR-R2 lists the task states, and FR-R3 says a task is ready only when every dependency is
  `done` or `skipped`. The PRD does not say where the shared run code lives, how the API and the
  orchestrator agree on subscriptions, or what makes run execution safe under at-least-once delivery.

## Decision

1. **A tenant-scoped checkpointer replaces `langgraph-checkpoint-mongodb`. This is a PRD conflict,
   reported in the handoff.** That library writes `checkpoints` and `checkpoint_writes` through its
   own client, filtered by `thread_id` alone and with no `org_id`, which breaks §7.9's data-access
   rule, the safety net, and the rule that checkpoints carry `org_id`. Instead,
   `onb_orchestrator.checkpointer.TenantCheckpointSaver` keeps the same storage model on `onb_data`
   tenant repositories:
   - Storage: one document per checkpoint (`run_checkpoints`) and one per pending write
     (`run_checkpoint_writes`), using LangGraph's own serializer.
   - Indexes: unique, and they start with `org_id`.
   - Thread check: the thread must be `"{org_id}:{run_id}"` for the org in the tenant context.
     Anything else raises `TenantViolationError` before any I/O.
   - Writes: a regular write is kept as first written (`$setOnInsert`), so a replayed task cannot
     change it. A special channel (error, interrupt) replaces its earlier write, as LangGraph's own
     savers do.
   - Interface: async only, with no thread deletion yet.
2. **Shared run code lives in `onb_agents.runs`.** This covers the state machines, the run and task
   records, `RunStore`, `RunStarter` and `run_bus`. The API (producer) and the orchestrator (consumer)
   both use it.
   - `run_bus(store)` is the only place the platform builds a `MongoEventBus`, with
     `SUBSCRIPTIONS = {"run.started": ("orchestrator.start_run",)}`. Both sides therefore write and
     claim the same jobs, and the cross-tenant suite enumerates one module.
3. **Starting a run is one outbox transaction.** `RunStarter` does it in this order:
   - It compiles the DAG and recomputes `CompiledDag.hash_of(document)` before saving. A mismatch is
     refused, because compiled tasks are read-only (ADR 0008).
   - It writes the run, which pins the template key, version, DAG document and hash, plus one
     `run_tasks` row per compiled task (skipped ones included) and the `run.start` audit record.
   - It publishes `run.started` with `change=` and the fixed key `run:<id>:started`. The payload
     holds only `{run_id}`.
   - It stores the request's `traceparent` on the run. Task spans continue from it (F-4), so every
     task joins the trace that started the run.
4. **The graph's shape comes only from the stored DAG.** `build_run_graph` first checks the run's DAG
   document against its hash (`stored_dag`), then builds the edges:
   - A task with no dependencies starts from `START`.
   - A task with one dependency follows it.
   - A task with several dependencies waits for all of them, using LangGraph's join
     `add_edge([deps], task)`. Separate edges would let a join run after only its shorter branch.
   - A task nothing depends on leads to `END`.

   Skipped tasks keep their node, so the graph has the template's shape, and their step does nothing.
   Nothing, the LLM included, adds, removes or reorders tasks (PRD §5.1).
5. **State changes go only through the state machine, as compare-and-set writes.**
   `check_transition` allows only the FR-R2 moves. `RunStore.move_task` writes with the status the
   decision was made on in its filter, so a concurrent writer gets `TransitionConflictError` and
   nothing is written. The task step re-checks FR-R3 itself rather than trusting the edges. Failed is
   terminal in RUN-1; retry and `blocked` belong to RUN-2, which extends these tables.
6. **The consumer is idempotent.** `start_run_handler`:
   - reads the run in the job's org (another org's run, or a missing one, is a no-op);
   - drives only `started`/`running` runs, leaving finished runs and later goals' states alone;
   - resumes from the checkpoint when one exists;
   - skips each task step whose work is already `done`;
   - never executes a task it finds `running`: only the delivery whose compare-and-set moved the task
     to `running` executes it. Another delivery raises a transient `TaskInProgressError` and is
     retried, so a task's work runs at most once per attempt. Taking over a task left `running` by a
     crashed worker needs a per-task lease, which is RUN-2's;
   - when a task fails, the DAG does not match its hash, or any deterministic error occurs (a blocked
     task, a broken graph), marks the run `failed` and cancels every unfinished task, including a
     sibling LangGraph cut off mid-step;
   - re-raises transient errors (the database, a compare-and-set race) for the bus to retry;
   - accepts a run already finished by an overlapping delivery as done;
   - lets a node's error reach the checkpoint (LangGraph stores it as a pending write) by its type
     only.

## Consequences

- Checkpoints obey the same tenancy rules as every other collection, with no separate path to
  audit. The cost is a small saver to maintain against LangGraph's `BaseCheckpointSaver` contract.
  `tests/test_checkpointer.py` runs a real graph through it, and `tests_int` runs it on the replica
  set.
- Checkpoints keep whole channel values per checkpoint rather than per-channel blobs. That is fine
  for RUN-1's state (a list of task ids). If agent subgraphs put large state in the parent graph,
  move to blob documents.
- RUN-2 must add thread retention or deletion, a graceful worker drain, and resume after a crash
  mid-task. RUN-3 replaces `StubExecutor` with the registered agents. HIL turns approval tasks into
  `interrupt()` plus `waiting_approval`.
