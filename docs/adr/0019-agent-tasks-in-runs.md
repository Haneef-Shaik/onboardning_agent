# ADR 0019 — Agent tasks in runs: grants, pinned models, run budgets, spans, sink rejections

- **Status:** accepted (RUN-3), 2026-11-12
- **Goal:** RUN-3 · PRD §7.1 (Sub-agents, Budgets, model recorded on the task), §7.10 (grants at task
  start), §7.9 Orchestrator, §5.7; ADR 0010 (agent runtime), ADR 0013 (sink), ADR 0015 (run execution)

## Context

RUN-1 left `TaskExecutor.execute(TaskWork)` as the seam where a task's work runs. RUN-3 plugs the
registered agents into it. Five things were open: where the executor gets a task's `pii_access`, when and
how it issues a grant, how a task keeps its model while the configuration changes, how a run's token
budget is shared and survives a restart, and how a sink's refusal fails the task (ADR 0013 leaves that to
RUN-3).

## Decision

1. **The executor reads the task from the run's hash-checked DAG** (`stored_dag`), not from `TaskWork`,
   the queue or the request. `TaskWork` is unchanged, so RUN-2 and RUN-3 do not conflict on it. The
   agent's input is the action, the template's `inputs` (which the template validator keeps free of PII)
   and the hire's id; a hire's values never reach an agent.
2. **A grant only for a declared `pii_access`.** `AgentTaskExecutor._grant` calls PII-4's `GrantIssuer`
   when, and only when, the template task declares `pii_access`: exactly its fields and its sink, for
   `subject_id` = the run's hire, with **the run's own `run_id`** (NOTIF-1's sink refuses any other as
   `wrong_run`) and a lifetime of the agent's `limits.timeout_s` + 30 s, at most 10 min. The grant goes
   to the runtime as `AgentTask.grant` and travels only as the `X-Onb-Pii-Grant` header (ADR 0010); it is
   never stored. It **fails closed**: a field that is not a vault field, a field the sink policy does not
   let that sink resolve, or no configured issuer fails the task (`failure.kind = grant_refused`) before
   the agent starts. A task without `pii_access` never reaches the issuer.
3. **The model is fixed once per task.** The first attempt resolves it (manifest → org override →
   default), and `RunStore.record_model` stores it on the task with a set-if-absent update. A later
   attempt of the same task (RUN-2's retries, a redelivery) rebuilds the *recorded* `connector` and
   `model` and never resolves the configuration again. A task that starts after a configuration change
   is a new task and takes the new model (PRD §7.1). Sampling params are not recorded (LLM-1 keeps them
   out of the store) and come from the manifest again.
4. **One per-run token budget.** `ORCHESTRATOR_RUN_MAX_TOKENS` (default 200 000) is a `Budget` shared as
   the parent of every task's budget in the process (so tasks running together count against it). After
   a restart it is rebuilt from the tasks' recorded `result.usage.tokens`. Exhausting it ends the
   running task `failed` with `failure = {kind: budget_exceeded, reason: "run tokens N/M"}`, and the
   run fails with its unfinished tasks cancelled (RUN-1). Tokens a crashed worker's in-flight task had
   not yet recorded are not counted after a restart (see open items).
5. **The outcome is on the task.** Before the runner moves the task, `RunStore.record_outcome` stores
   `result` (outcome, output, usage, model, trajectory length) and, for a failed agent, `failure =
   {kind, reason}`: codes and counts, never message text. The trajectory itself is not stored on the task.
   A `refused` or `needs_human` answer is a completed task with its answer stored; HIL-1 owns turning
   `needs_human` into `waiting_approval`.
6. **Spans:** `orchestrator.run` (continues the request's stored traceparent) → `orchestrator.task` →
   `agent.run` → `guardrails.invoke` → `llm.chat`, and `agent.run` → `tool.call`. A task span is a child
   of the current run span, and falls back to the stored traceparent when no run span is current. Span
   attributes are ids only.
7. **`task.sink_rejected` fails the task** through a new consumer, `orchestrator.fail_task`. It fails the
   payload's task if it is `running`, or, when the sink did not know the task, every `running` task of
   that agent in the run (all running tasks if no agent is named): fail closed. The reason code is
   stored on the task. A finished task, a task of another org (the read is tenant-scoped) and a malformed
   payload change nothing. The runner treats a compare-and-set conflict on its final `done` move as
   "failed from outside" and ends the task `failed`, so an agent's late success never undoes a sink's
   refusal. The subscription is in **both** maps: `onb_agents.runs.SUBSCRIPTIONS` (the orchestrator's bus)
   and `onb_contracts.notify.NOTIFY_SUBSCRIPTIONS`, because the sink that publishes the event builds its
   bus from the latter and the outbox writes one job per subscriber at publish time. Without the second
   entry no job would exist. The consumer name lives once, in `onb_contracts.notify`.
8. **The default executor stays the stub.** `ORCHESTRATOR_EXECUTOR=agents` selects the agents (and then
   `CONFIG_DIR`, `AGENT_TOKEN_KEY`, `MCP_HUB_URL` and `PII_GRANT_SIGNING_KEY` are required; startup fails
   fast naming the variable). No model is called unless it is chosen; tests use LLM-1's `fake`
   connector.

## Consequences

- Retired or unregistered agents fail their task (`no_agent`, `retired_agent`) instead of raising in the
  runtime; the run fails visibly.
- RUN-2 adds retries on top: a retried attempt keeps the model (3) and the run's token count (4), but
  must clear a task's stale `failure` when it re-enters `running` (5).
- A sink rejection fails the *task* at once; the *run* fails when its driver sees the failed task. If no
  worker is driving the run (a crash), RUN-2's recovery must pick it up.
- Two tests that asserted an exact subscription map (`test_run_store`, `test_notify_contracts`) and the
  worker's consumer list now include `orchestrator.fail_task`.

## Open

- The in-process budget cannot see tokens used by tasks running in *other* worker processes. A second
  worker needs the run's usage recorded as tasks progress (not only at their end).
- The agent trajectory is not kept on the task; an operator reads it from the trace and the audit log.
