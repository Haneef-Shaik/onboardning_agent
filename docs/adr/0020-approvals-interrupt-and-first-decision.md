# 0020 · Approvals: `interrupt()` pauses the task, one write decides it, the first decision wins

- **Status:** accepted · 2026-11-12 · goal HIL-1
- **Context:** PRD FR-H1-H3 and FR-H5 want an approval task to pause its branch, the approver notified in
  the web inbox and on the Telegram mock, the first decision to win and resume the run, and a
  `requires_approval` tool call to become an approval task. RUN-3 left `needs_human` answers completing their
  task; NOTIF-1 left the `decision.received` event without a subscriber and two open findings (a press is not
  bound to the expected chat; buttons never expire). ADR 0015 reserved the pause for HIL: "`interrupt()` plus
  `waiting_approval`".

## Decision

1. **One record, three reasons.** An approval (`approvals`, tenant collection; the shape the eval fixtures
   already use) is opened for a task for one reason: `task` (an `approval` task of the template, FR-H1),
   `tool_call` (an agent's call the hub refused with `approval_required`, under the manifest's
   `requires_approval` rule, FR-H5) or `needs_human` (the agent answered `needs_human`). Its id is a hash of
   (run, task, kind, rule), so a redelivered job finds the approval it opened. It holds the approver (a user id,
   or a role: any user with it may decide), the masked `packet`, and, once decided, `{decision, comment,
   channel, decided_by, decided_at}` (FR-H3). The approver comes from the template task's `approver` rule (the
   hire's `manager_id`, a role, or a user id); a task without a rule goes to the hire's manager, then to HR.
2. **The pause is LangGraph's `interrupt()`.** The runner opens the approval, and in **one transaction** moves
   the task `running → waiting_approval` and publishes `approval.requested`. The graph node then calls
   `interrupt({approval_id})`: the graph checkpoints (ADR 0015's tenant checkpointer), `orchestrator.start_run`
   returns **without finishing the run**, and the run stays `running` with nothing after that task started.
   A task that waits is not an error and not a failure.
3. **Resume reads the stored decision.** `approval.decided` → `orchestrator.resume_approval` resumes the paused
   node with `Command(resume={interrupt id: {approval_id}})`. The node runs again from its top, finds the task
   `waiting_approval`, gets the resume value and `settle`s the task from the **stored** approval, never from the
   event: `approved` finishes the task (`task` / `needs_human`) or runs it again with the rule approved
   (`tool_call`); `rejected` fails it (`failure = {kind: approval_rejected, reason: reject}`), which fails the run
   as any failed task does. Settling can run the task again and meet the next approval: the node pauses again.
   A decision that lands before the start job has checkpointed its pause, or while the same run's other approval
   is being resumed (a per-run lease, `resume_until`), is **retried** by the bus (transient `TaskStepError`),
   never dropped.
4. **One decision write; the first wins.** `ApprovalDecider.decide` is the only way to decide. In one outbox
   transaction it applies the decision to a `pending` approval (compare-and-set), writes the audit record
   (`approval.decide`, reason `<decision>:<channel>`) and publishes `approval.decided` under the fixed
   idempotency key `approval:<id>:decided`. A second decision either finds the key (the bus writes nothing) or
   loses the compare-and-set (the transaction rolls back): `AlreadyDecidedError`. The web route answers it
   **409**; the Telegram consumer logs it and acknowledges. Only the approver decides: anyone else, another
   org's user included, gets 404, the answer for an approval that does not exist.
5. **Telegram.** The approver is told by `notify.approval` (in `mcp-mocks`, where the Telegram mock and the
   in-app channel live; the orchestrator may not import an app) on `approval.requested`: an in-app
   notification and a mock message with **Approve** and **Reject** buttons through `TelegramMockChannel`,
   `ref = {approval, id}` and `audience_user_id = the approver`. A press reaches the API webhook
   (`decision.received`, NOTIF-1); `orchestrator.record_decision` loads the outbox message the event names and
   requires its `ref` to be that approval, attributes the decision to the message's `audience_user_id` (the
   user the buttons were sent to, never anything in the press), and calls the same `decide`. The mock's chat id
   is derived from the org and user ids (no mobile is involved).
6. **Agents.** For an `approval` task the agent is the Approvals Agent: its input names the approval, its packet
   (`ApprovalPacket`, masked output) is stored on the approval, and a packet it cannot produce (a failure, a
   refusal, `needs_human`) is recorded as a code (`packet_failure`) while the human is **still asked**: a missing
   summary must not block a decision. For any other task, `needs_human` and a refused `requires_approval` call
   raise `ApprovalRequestedError`; the rule is the manifest's own (a denial the manifest has no rule for opens
   nothing). An agent's `approved:<rule>` scopes come **only** from the stored approved `tool_call` approvals of
   that task (ADR 0010 §7); an approved rule the hub still refuses fails the task `approval_ineffective`
   instead of waiting again.
7. **Events and consumers.** `approval.requested` → `notify.approval`; `approval.decided` →
   `orchestrator.resume_approval`; `decision.received` → `orchestrator.record_decision`. Each is in **both**
   subscription maps (`run_bus`'s `SUBSCRIPTIONS` and `onb_contracts.notify.NOTIFY_SUBSCRIPTIONS`) because a
   worker may only run consumers its own bus subscribes, and because the webhook that publishes
   `decision.received` builds its bus from the notify map. `run_bus(store)` stays the only builder of the
   run-side bus; the API's approval service takes it as an argument.
8. **NOTIF-1's two findings.** *Buttons never expire:* a button on a decided or cancelled approval is inert,
   because the decision is a compare-and-set on `pending` (FR-H3 first decision wins). That is the expiry the
   product needs; a time-based expiry is HIL-2's SLA. *A press is not bound to the expected chat:* **not
   closed.** The mock chat id is derived, not a real Telegram chat, and the press payload deliberately carries
   no Telegram id (ADR 0013), so there is nothing to compare; it needs the real adapter's `/start`-bound chat
   ids (DEP-1 / the real Bot API adapter). What binds a press today is the button id (128 random bits), the
   stored message's `ref` and its `audience_user_id`.

## Alternatives rejected

- **Resume the run directly from the decision handler.** A crash between the decision and the resume would lose
  the resume. The decision and `approval.decided` commit together; the resume is a consumer with retries.
- **Decide from the event payload.** The bus is a trust boundary (RUN-3's finding for SEC-2); the resume value and
  the event carry ids, and the decision is read from the database.
- **Move the notifier into the orchestrator.** It would import `mcp-mocks` (app → app) or reimplement the
  channels. The sink service already hosts the in-app consumer and the Telegram mock.
- **A new task record for an approval created by a tool call.** Compiled tasks are read-only (ADR 0008, PRD
  §5.1: nothing adds tasks to a run). The "approval task" of FR-H5 is the approval record the task waits on.

## Consequences

- LangGraph halts the whole graph at the end of the superstep an interrupt happens in: a branch that is not in
  that superstep waits too, so "pauses its branch" means: everything after the waiting task, and, in the same
  step, nothing that depends on it. Parallel independent branches that have not started yet start after the
  approval. A template author who needs them to run first orders them first.
- A task may open at most **two** approvals (`MAX_APPROVALS_PER_TASK`): LangGraph replays a node's earlier resume
  values, so a longer chain inside one node cannot be resumed reliably. A third request fails the task
  `approval_limit`, visibly. A failed run cancels its still-pending approvals.
- The interrupted node runs from its top on resume; it is idempotent by construction (it reads the task state).
- A template task's `requires_approval: true` flag has no runtime effect yet beyond naming where an `approver`
  rule may sit (the validator). Enforcement is the agent manifest's `requires_approval` rules at the hub. (Found
  for TPL / DEP: decide whether the flag should open an approval before the task.)
- Latency: each bus hop polls at 1 s (`QueueSettings.poll_seconds`). A web decision is one hop
  (`approval.decided` → resume); a Telegram press two (`decision.received` → record → `approval.decided` →
  resume). Measured in `docs/evidence/HIL-1/`.
- HIL-2 uses `sla_hours` and `requested_at` on the approval; UI-6 reads `GET /approvals`; RUN-2's cancel should
  call `ApprovalStore.cancel_pending(run_id)`; DEP-1's orchestrator service runs the two new consumers (no new
  settings) and mcp-notify runs `notify.approval`.
- `waiting_approval → running` is a new legal task move (an approved tool call runs the task again); it does not
  count as another attempt.

## Amendment (rebase onto RUN-2, ADR 0018, and AGT-1)

- The approval resume consumer is **`orchestrator.resume_approval`**: `orchestrator.resume_run` is RUN-2's (`run.resumed`).
- An attempt that asks a human parks the task (leaving `running` releases its lease) and is not an attempt's
  failure; an approved tool call runs the task again under a new lease in the same attempt, through RUN-2's retry
  loop. A rejection fails the task and the run (nothing to retry). A decision made while the run is paused or
  blocked is stored and settled by the driver when the run is driven on (`_settle_decided`).
- Cancelling or failing a run cancels its pending approvals (`orchestrator.cleanup_run`, `_Driver.finish`).
- The agent runtime's inner graph is compiled with `checkpointer=False`: it must not inherit the run graph's
  checkpoint, or an approved re-run would resume the first attempt's stale state.
