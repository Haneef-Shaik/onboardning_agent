# Handoff — HIL-1 · Pause for approvals and resume on the first decision        attempt 3 · goal/hil-1-approvals-hil · rebased onto main c307fe9 · HEAD: see `git log -1`

**Outcome claimed.** An `approval` task pauses its run with `interrupt()`; the Approvals Agent's masked packet is stored on the approval; the approver is told in-app and on the Telegram mock; the first decision (web or Telegram) wins and resumes the run; a refused `requires_approval` tool call, and a `needs_human` answer, become approvals. Design: `docs/adr/0020-approvals-interrupt-and-first-decision.md`.

**Attempt 3 = the same work rebased onto main `c307fe9`** (RUN-2 run control, ADR 0018; AGT-1 agents in runs, ADR 0021; RUN-3 and NOTIF-1 already inherited). The branch was squashed to one commit on its old base and then rebased (one conflict resolution instead of fourteen), so the earlier per-step commits are gone; the pre-rebase tip is the local branch `backup-hil-1-pre-rebase`. See "Rebase onto main" below. Verifier history: attempts 1 and 2 (pre-rebase) failed only box 1 (no Bedrock) and, in attempt 1, box 3 for a missing hardware statement (fixed).

Attempt 1 verifier verdict: fail on box 1 (no Bedrock) and on box 3 only for a missing hardware statement, now added to `resume-latency.json`. Every other box was verified.

**Box 1 is NOT met**, only for lack of Bedrock (OW-1). Everything else is proven. No model was called.

Gates ran on compose stack `ob-hil-1` (ports 19600/19601). Evidence is in `docs/evidence/HIL-1/`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `ap-001`…`ap-003` meet the eval gates on the real connector (report in `docs/evidence/HIL-1/`) | **no** (waiting on OW-1) | Interim, fake/scripted connector only: `make evals-selftest` → `selftest evr_20261112T162850Z_05954c5d: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`; the `ap-*` slice is `ap-selftest-fake.json` (3/3 each). What remains, exactly: `box1-remaining.md`. |
| Concurrent web and Telegram decisions record exactly one; the other gets 409 (test) | yes | `libs/py-agents/tests/test_approvals.py::test_concurrent_decisions_record_exactly_one`; `apps/orchestrator/tests/test_record_decision.py::test_a_web_decision_and_a_telegram_press_at_once_record_exactly_one`, `::test_the_web_decision_after_a_telegram_press_*`; `apps/api/tests/test_approvals_route.py::test_a_web_decision_after_a_telegram_press_is_409`, `::test_a_telegram_press_after_a_web_decision_is_refused_and_the_web_decision_stands`, `::test_the_second_decision_is_409_and_the_first_stands`; on real Mongo transactions: `apps/orchestrator/tests_int/test_approvals_against_mongo.py::test_concurrent_web_and_telegram_decisions_record_exactly_one_on_the_real_database`, `::test_many_concurrent_decisions_record_one` (8 concurrent: 1 recorded, 7 `AlreadyDecidedError`, one `approval.decided` event). |
| Decision → run resumed is under 5 s at p95 over 30+ decisions on the compose stack (measurement in `docs/evidence/HIL-1/`) | yes | `docs/evidence/HIL-1/resume-latency.json` (`tests_int/test_resume_latency.py`): 36 decisions (18 web, 18 Telegram), **p50 1.030 s, p95 1.087 s, max 1.170 s**, on an Apple M5 (10 CPUs, 16 GiB RAM), Docker 29.8.2 (10 CPUs, 7.7 GiB); the report records the hardware (verifier attempt 1 asked for it). The clock is the approver's act (web `decide` / Telegram `decision.received` publish) to the next task started; the orchestrator worker is the production one (`prepare`, 1 s poll) in-process against the compose Mongo (no orchestrator compose service exists yet, DEP-1), stub executor. |
| A `requires_approval` tool call creates an approval task (test) | yes | `apps/orchestrator/tests/test_agent_approvals.py::test_a_requires_approval_tool_call_creates_an_approval_task` (hub-style `denied (approval_required)` → approval `kind=tool_call`, rule, approver, task `waiting_approval`, `approval.requested` published), `::test_an_approved_tool_call_runs_the_task_again_with_that_rule_in_the_agents_token`, `::test_a_rejected_tool_call_never_gets_its_rule_approved`, `::test_a_call_the_hub_denies_for_another_reason_is_not_an_approval`, `::test_a_refused_call_that_matches_none_of_the_manifests_rules_is_not_an_approval`; run-level: `test_approvals_in_runs.py::test_a_requires_approval_tool_call_opens_an_approval_and_an_approve_runs_the_task_again`. |
| `make test-xt` covers every new route | yes | `docs/evidence/HIL-1/make-test-xt.txt`: `cross-tenant surface: 30 API routes, 9 MCP tools, 6 event consumers`; rows for `GET /approvals`, `POST /approvals/{approval_id}/decision` and the consumers `orchestrator.resume_approval`, `orchestrator.record_decision`, `notify.approval`; exit 0 (an earlier run had one setup error, the known Mongo "connection closed" flake; re-run clean). |

## Rebase onto main (RUN-2, AGT-1): how the two designs were reconciled
| Area | Decision |
|------|----------|
| **Consumer name clash** | RUN-2 owns `orchestrator.resume_run` (for `run.resumed`: drive a paused/blocked run on). Mine is **`orchestrator.resume_approval`** (`approval.decided`; constants `RESUME_APPROVAL`, `RESUME_APPROVAL_CONSUMER`, handler `resume_approval_handler`). Both are in `run_bus`. |
| **Task leases, retry and block (FR-R4)** | An attempt that asks a human (`ApprovalRequestedError`, or an `approval` task that finished) is not a failure and not retried: `_attempts` parks the task in `waiting_approval` (leaving `running` releases its lease). An approved tool call runs the task again under a **new lease in the same attempt** (`waiting_approval → running` takes `fields={**lease, error: None, retry_at: None, failure: None}` and does not count as an attempt) through RUN-2's own `_attempts` loop, so retry/backoff/takeover apply to the re-run. Agent failures still retry then block the run. A **rejection** fails the task (`failure.kind = approval_rejected`) and the run: nothing to retry (HR can still complete the task by hand). |
| **Driver** | `_Driver.drive` and a new `_Driver.resume` return whether the graph paused; `orchestrator.start_run` and `resume_approval` share one guarded path (`_drive_guarded`: drain, halt, exhausted → block, transient, fail, complete). A paused graph is never completed. |
| **Pause / block / cancel** | A decision made while the run is paused or blocked is stored; the resume job leaves the run alone; when HR resumes it, `drive` first resumes every interrupt whose approval is already decided (`_settle_decided`), so it never pauses again for a decision already made. **Cancel is wired**: `orchestrator.cleanup_run` (on `run.cancelled`) and a failed run both call `ApprovalStore.cancel_pending`. |
| **`move_task` extras** | RUN-2's `fields=` replaces my `extra=`; my attempt rule (no increment from `waiting_approval`) stays. |
| **AGT-1** | `agent_input` is used as is; the Approvals Agent's `approval_id` is added on top (`AgentTaskExecutor._input`). The seed-template integration test (`apps/mcp-hub/tests_int/test_task_agents_int.py`) now decides the one approval and resumes (the approval task is the one human step): 6/6 non-approval tasks still complete without human action. Its platform passes the outbox stand-in to the drivers (that stack's Mongo has no replica set). |
| **A bug the merge exposed (fixed)** | The agent runtime's inner graph was compiled without a checkpointer, so inside a run it **inherited the run graph's** and an approved re-run resumed the first attempt's stale state. `libs/py-agents/src/onb_agents/runtime/graph.py` now compiles with `checkpointer=False` (an agent task is atomic). |
| **Exact-list tests: extended, never loosened** | `test_run_store.py` (subscriptions, consumers), `test_worker_main.py`, `test_agents_setup.py` (consumer tuples), `test_notify_contracts.py`, `test_notify_main.py`, role matrix and xt tables now list RUN-2's entries and mine. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `approvals` records: `{_id: appr_<hash>, run_id, task_id, hire_id, kind: task\|tool_call\|needs_human, rule, status: pending\|approved\|rejected\|cancelled, approver_id \| approver_role, title, request.items, packet, packet_failure, requested_at, sla_hours, decision{decision, comment, channel, decided_by, decided_at}}`; one approval per (run, task, kind, rule). HIL-2 reads `sla_hours`, `requested_at`, `status`. | `libs/py-agents/tests/test_approvals.py`, ADR 0020 §1 |
| `ApprovalDecider.decide` is the only decision write: compare-and-set on `pending` + audit record `approval.decide` (reason `<decision>:<channel>`) + `approval.decided`, one outbox transaction, fixed key `approval:<id>:decided`. | `test_approvals.py`, `tests_int::test_a_decision_whose_audit_write_fails_is_not_made_and_publishes_nothing` |
| An approval task / `needs_human` / refused `requires_approval` call parks the task in `waiting_approval` (new legal move `waiting_approval → running`, not a new attempt) and the run stays `running`; the pause survives a restart. | `test_approvals_in_runs.py`, `tests_int::test_the_run_is_paused_not_finished_before_the_decision`, `::test_the_pause_survives_a_restart_of_the_whole_stack` |
| Resume reads the stored decision, never the event; a decision that arrives before the pause is checkpointed is retried (transient), never dropped; an approved tool call runs the task again with `approved:<rule>` scopes from stored approvals only. | `test_approvals_in_runs.py`, `test_agent_approvals.py` |
| `GET /approvals?mine=true\|false&status=&page=&limit=` and `POST /approvals/{id}/decision {decision, comment?}`: 200 / 404 (missing, other org, not the approver) / 409 (already decided) / 422. UI-6 builds on these. | `apps/api/tests/test_approvals_route.py` (26), role matrix, xt rows, `make contracts-check` |
| A Telegram press is attributed to the outbox message's `audience_user_id`, only when the message's `ref` is that approval. `notify.approval` sends the approver an in-app notification and a Telegram-mock message with Approve/Reject buttons (`audience_user_id` = approver), naming the granted rule for tool-call approvals. | `test_record_decision.py`, `apps/mcp-mocks/tests/test_notify_approval.py` |
| A task opens at most two approvals; a third fails it `approval_limit`. A failed run cancels its pending approvals. | `test_approvals_in_runs.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U5, NF-LAT, EV-AP @ MS-6 | compose, 2026-11-12 | contributes: approval request + one-action decision (U5), decision→resume latency (NF-LAT), `ap-*` on the fake connector only (EV-AP, real-connector run pending) | `resume-latency.json`, `ap-selftest-fake.json` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | exit 0 on the rebased tree (see `gates.txt`, rebased-tree section) |
| Integration | `make test-int` | exit 0 on the rebased tree (first run failed one AGT-1 test that now meets a real approval, fixed above; re-run exit 0). orchestrator:test-int includes the 9 approval Mongo tests and the latency test |
| Cross-tenant | `make test-xt` | part of `make check`, exit 0 on the rebased tree (route and consumer rows for RUN-2's and mine; `make-test-xt.txt` is the pre-rebase listing) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Eval self-test | `make evals-selftest` | rebased tree: `selftest evr_20261112T182011Z_9a88836f: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS`. The real eval run needs Bedrock (OW-1). |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` (part of `make check`) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-1 (ADR 0015) | `run_bus(store)` is the only run-side bus builder; the tenant checkpointer persists the paused graph | yes: the API's `ApprovalService` and the worker take it as an argument. |
| RUN-3 (ADR 0019) | `TaskExecutor.execute(TaskWork)` is the seam; agent tasks store their outcome | yes; `TaskWork` unchanged. RUN-3's "`needs_human` completes the task" is replaced as asked. |
| NOTIF-1 (ADR 0013) | `decision.received {ref_type, ref_id, decision, outbox_id, …}`; `TelegramMockChannel.send(..., buttons, ref, audience_user_id)` | yes. |
| AG-1 (ADR 0010 §7) | `approved:<rule>` scopes only from persisted HIL approvals of that task | yes: `ApprovalStore.approved_rules`. |
| HUB-1 | `approval_required` denial for a `requires_approval` rule without the scope | yes (test double, same text). |

## Mutation checks
`docs/evidence/HIL-1/mutate.py` over the merged code: 55 mutations. Full run `mutation-checks-full-run.txt`: 53 killed, `source diff after all mutations: 0 files differ`; two survivors: **M49** (an approved re-run takes no lease) got a test (`test_an_approved_tool_calls_re_run_holds_a_lease_like_any_running_task`) and is killed in `mutation-checks-rerun.txt`; **M55** (the resume handler ignores a paused run) is a documented **equivalent mutant**: RUN-2's `_ensure_runnable` halts a paused run before the resumed node does anything, so the handler's early return has no observable effect (defence in depth). Result: 54 killed + 1 equivalent, no unexplained survivor. New merge mutants: M49-M55 (lease on re-run, cancel of pending approvals on failure and on cancel, settling decisions made while paused, agent-graph isolation, approval limit). Earlier history (pre-rebase 48/48, with the survivors that each got a test) is unchanged. The suite for M1-M3 includes the Mongo integration tests (needs `.env`).

## Review findings left open
Two independent reviews (general-purpose agents: code, security) on `git diff main...HEAD`. No CRITICAL or HIGH. Fixed, each with a test: security MEDIUM (an approver could not see that a tool-call approval grants a rule for the whole task → message names kind and rule); code MEDIUM (a transient failure after an approved re-run started was acknowledged as done → `ResumeInterrupted` transient); code MEDIUM (a chain of 3+ approvals in one node cannot be resumed reliably because LangGraph replays resume values → limit of 2, third fails the task `approval_limit`, visibly); code LOW (siblings left pending when a run fails → cancelled).

Left open:
- **MEDIUM (NOTIF-1's finding, not closed) · `apps/api/src/onb_api/notify/service.py`, `consumer.py` (`record_decision`)**: a Telegram press is bound only by the 128-bit button id, the stored message's `ref` and its `audience_user_id`, not by the presser's chat (the mock's chat id is derived, the press carries no Telegram id by design). Must be closed with the real Telegram adapter (DEP-1). ADR 0020 §8. The other half of the finding (buttons never expire) is closed: a button on a decided/cancelled approval is inert (first decision wins).
- **LOW · `runner.py` `_open_approval`/`list_for`**: a `task` approval is created `pending` just before the agent prepares the packet, so `GET /approvals` can list it, and it can be decided, before the task parks (no packet yet). The decision is not lost (`_wait_for_approval` settles an already-decided approval); the approver just decides without a summary. Fix if it matters: filter the list to approvals whose task is `waiting_approval`.
- **LOW · `runner.py` `_wait_for_approval`**: a tool-call approval decided in the millisecond window between its creation and the park is treated as already applied (`approval_ineffective`).
- **LOW · `consumer.py` resume lease**: the lease (120 s) does not cover the start job, and an agent re-run longer than 120 s could overlap a second resume. Size the TTL to the agent's `timeout_s` if long agents arrive.
- **LOW · `record_decision`** takes the decision from the event payload (a bus publisher with a valid outbox id and ref could pick approve/reject): the bus is a trust boundary (SEC-2, already recorded by RUN-3).
- **LOW · `decide.py`**: the free-text comment is stored raw and shown to HR (human-entered; not redacted); `auditor` is optional on `ApprovalDecider` (every production call site passes one; make it required in SEC-1).
- **LOW · `agent_executor.py`**: a prompt-injected agent can answer `needs_human` and open one approval per task (bounded pager noise; HIL-2/TPL).
- **LOW**: packet text (model output) reaches a human channel after regex redaction only; labelled "AI recommendation", deterministic fields (kind, rule, hire) come first.

## Left undone, and why
- Box 1 (real-connector eval): needs OW-1. `box1-remaining.md` lists the exact commands and gates.
- No orchestrator compose service, so the latency measurement runs the production worker in-process against the compose Mongo (DEP-1 adds the service).
- `/goal` could not be set from this orchestrated session; I worked to the text `plan.py goal HIL-1` printed.

## Found for other goals
- **RUN-2 / run control**: cancel and failure already cancel pending approvals (wired here). HR "retry" of a task failed by a **rejection** re-asks nothing (the approval id is deterministic and already rejected, so the retry fails again): use "complete by hand", or reopen the approval in a later goal if HR needs a re-ask.
- **UI-6**: inbox = `GET /approvals`; the record carries `packet`, `items`, `kind`, `rule`, `decision`.
- **HIL-2**: `sla_hours` + `requested_at` + `status=pending`; escalate to HR role approvals (`approver_role`).
- **DEP-1**: the orchestrator service runs the new consumers with no new settings; mcp-notify runs `notify.approval` (already in `notify.main`); the real Telegram adapter must bind presses to a real chat (above).
- **TPL / DEP**: a template task's `requires_approval: true` flag has no runtime effect (only the agent manifest's `requires_approval` rules at the hub do); decide whether the flag should open an approval before the task.
- **SEC-1/SEC-2**: the items under "Review findings left open".

## Decisions forced (ADRs written)
- `docs/adr/0020-approvals-interrupt-and-first-decision.md`. Shared-file changes, all additive: `onb_contracts.notify` (5 constants, two subscription entries), `run_bus` `SUBSCRIPTIONS` (3 entries), `RunStore` resume lease (RUN-2's `fields=` carries the task extras), `states.py` (one move), `create_app(approvals=)`, `build_api` (xt), worker (2 handlers). Existing tests that pinned exact lists were updated: `test_run_store.py`, `test_run_states.py`, `test_notify_contracts.py`, `test_worker_main.py`, `test_agents_setup.py`, `test_notify_main.py`; the RUN-1 xt case `test_the_orchestrator_completes_an_org_a_run_entirely_in_org_a` now decides the seed template's approval before asserting completion (the approval task pauses the run, as intended).

## Traps hit
- A worker may only run consumers its own bus subscribes: new consumers go in both subscription maps.
- LangGraph replays a node's earlier `interrupt()` resume values: do not chain more than two interrupts in one node; `settle` reads the database, not the resume value.
- py-agents and orchestrator unit tests cannot run in one pytest call (known); `make test-xt` takes ~8 min and hit the known "connection closed" flake once.
