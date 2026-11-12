# Handoff — HIL-2 · Remind and escalate overdue approvals        attempt 1 · goal/hil-2-approval-sla · HEAD: see `git log -1` (base main 5f61a29)

**Outcome claimed.** An undecided approval reminds its approver once when it is overdue and escalates to HR
once at a second threshold. Both thresholds come from `config/approval_sla.yaml`. Each stage is stamped on
the approval in the same outbox transaction that publishes it, so a restarted worker never repeats one.
Design: `docs/adr/0027-approval-sla-reminder-and-escalation.md`.

**Entry gate.** HIL-1 is `on hold` on the board, not `done`. Its code is merged to main (5f61a29), and only its
real-connector eval (box 1, OW-1) is open. The board note says "HIL-2 can build on" its routes and records,
and Haneef's orchestrator started this run with HIL-1 named as its base. HIL-2 uses only HIL-1's records and
consumers, never its eval.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| With a fake clock, an overdue approval sends one reminder, then escalates to HR at the second threshold (test) | yes | **Sweep, fake clock:** `apps/orchestrator/tests/test_approval_sla.py::test_an_overdue_approval_reminds_once_then_escalates_to_hr_at_the_second_threshold`. A 10 h SLA: nothing at 0/5/9.99 h; one reminder at 10 h; nothing at 10-19.99 h; one escalation (`escalate_to: HR`) at 20 h; nothing at 21 h/48 h/30 d. The record stays `pending` with `reminded_at`/`escalated_at` set. Also `::test_an_approval_first_seen_past_both_thresholds_is_reminded_then_escalated_in_order`, `::test_a_decided_or_cancelled_approval_is_never_reminded`, `::test_a_decision_that_lands_during_the_pass_wins_and_nothing_is_sent`, `::test_a_backlog_of_old_approvals_not_yet_due_does_not_starve_a_newer_one`. **Delivery:** `apps/mcp-mocks/tests/test_notify_approval_sla.py` (27). The reminder goes to the approver in-app and on Telegram with Approve/Reject; the escalation goes to every HR user, without buttons, and not to the approver. **End to end on the compose Mongo:** `apps/orchestrator/tests_int/test_approval_sla_against_mongo.py::test_a_paused_approval_is_reminded_once_then_escalated_once_and_a_restart_repeats_nothing`. A real run pauses on its approval (`sla_hours: 48`). The sweep, on a fake clock at 1/47.9/48/48.1/60/95.9/96/97/500 h, gives exactly one reminder at 48 h and one escalation at 96 h. mcp-notify's real worker delivers one reminder to the manager and one escalation to each HR user. Command: `uv run --frozen pytest apps/orchestrator/tests/test_approval_sla.py -q` → `15 passed`; `uv run --frozen --env-file .env pytest apps/orchestrator/tests_int/test_approval_sla_against_mongo.py -q` → `3 passed`. |
| Thresholds come from config (test) | yes | `libs/py-agents/tests/test_approval_sla.py::test_the_thresholds_come_from_the_config_file`, `::test_a_changed_config_file_changes_when_an_approval_is_due`, `::test_the_default_sla_is_the_configured_one_not_a_constant`, `::test_the_shipped_config_file_loads`, `::test_a_bad_config_fails_fast` (15 bad files). `apps/orchestrator/tests/test_approval_sla.py::test_the_sweep_follows_the_thresholds_in_the_config_file`: a file with 0.5×/1.5× and `HR_ADMIN` moves the reminder to 5 h, the escalation to 15 h, and the role to HR_ADMIN. `apps/orchestrator/tests/test_worker_main.py::test_the_approval_sla_thresholds_come_from_the_config_file_the_environment_names` (`APPROVAL_SLA_CONFIG`), `::test_without_the_variable_the_shipped_config_file_is_read`, `::test_a_bad_approval_sla_config_fails_startup`, `::test_a_missing_approval_sla_config_fails_startup`. Command: `uv run --frozen pytest libs/py-agents/tests/test_approval_sla.py -q` → `29 passed`. |
| Reminders aren't repeated after a worker restart (test) | yes | `apps/orchestrator/tests/test_approval_sla.py::test_a_restarted_worker_does_not_remind_or_escalate_again`: a new store and a new job, with nothing carried over, at each later time. `::test_two_workers_sweeping_at_once_send_one_reminder`; `::test_a_reminder_whose_event_was_published_but_not_stamped_is_not_sent_again` (the fixed key, then re-stamp and escalate); `::test_a_lost_stamp_past_both_thresholds_still_escalates_in_the_same_pass`. On the real replica set, each sweep is a fresh connection, bus and job (a restart): `tests_int/test_approval_sla_against_mongo.py::test_a_paused_approval_is_reminded_once_then_escalated_once_and_a_restart_repeats_nothing`. Also `::test_a_stamp_whose_transaction_fails_is_rolled_back_with_its_event_and_sent_once_later`: the worker dies inside the transaction after stamping, leaving neither stamp nor event; the next pass sends the reminder exactly once. Notifier redelivery: `test_notify_approval_sla.py::test_the_same_reminder_delivered_again_tells_the_approver_once`, `::test_the_same_stage_published_again_under_another_event_tells_each_user_once`. Worker shutdown: `test_worker_main.py::test_the_sweep_stops_when_the_worker_is_asked_to_not_after_its_drain`, `::test_a_stuck_sweep_is_cancelled_after_the_drain_window`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Approvals carry `reminded_at` / `escalated_at` (datetime or absent). Each is set once, while `pending`, by `ApprovalStore.mark_stage` (a compare-and-set). They are not yet in `ApprovalRecord` or the OpenAPI document (UI-6 may add them for an "overdue" badge). | `libs/py-agents/tests/test_approval_sla.py::test_a_stage_is_stamped_once_and_only_while_pending` |
| `approval.overdue {approval_id, run_id, task_id, stage: reminder\|escalation, escalate_to: HR\|HR_ADMIN\|None}` is published once per approval and stage, key `approval:<id>:<stage>`, in both `run_bus` SUBSCRIPTIONS and `NOTIFY_SUBSCRIPTIONS` → `notify.approval_sla`. | `test_run_store.py`, `test_notify_contracts.py`, `test_notify_main.py` |
| In-app note kinds `approval.reminder` (approver) and `approval.escalated` (HR). Telegram-mock outbox documents carry `sla_stage`; reminders keep the Approve/Reject buttons; escalations have none. | `test_notify_approval_sla.py` |
| The orchestrator worker runs the sweep beside its consumers. `APPROVAL_SLA_CONFIG` (default `config/approval_sla.yaml`) is read at startup, and a bad file stops startup. | `test_worker_main.py` |
| Escalation never reassigns or decides: the approval stays `pending` with its approver (HIL-1's "only the approver decides" holds). | `test_an_escalation_tells_every_hr_user_and_not_the_approver`, box 1 int test (`status == pending`) |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none: `plan.py show HIL-2` lists no ledger rows) | | | | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (stack `ob-hil-2`, 19600/19601) | Every lint, typecheck and unit target passed in all three runs; test-xt hit the **known Mongo "connection closed" / ServerSelectionTimeout setup flake** each time (three stacks share 7.7 GiB of Docker memory). Run 1: `352 passed, 3 errors` (log not kept: rtk truncated it). Run 2: `354 passed, 1 error` (`docs/evidence/HIL-2/make-check-run2-flake.txt`). Run 3: `348 passed, 7 errors` (`make-check.txt`, copy in `make-check-run3-flake.txt`). Every error is a `tools/tests_xt/test_xt_api.py` setup error on an API route this diff does not touch. `cross-tenant surface: 41 API routes, 9 MCP tools, 9 event consumers` (incl. `consumer notify.approval_sla`). |
| Cross-tenant re-run | the 7 errored ids + `test_xt_consumers.py` + `test_xt_coverage.py` | `34 passed`, exit 0 (`docs/evidence/HIL-2/test-xt-retry.txt`) |
| Project test-xt | `nx run-many -t test-xt` (the half of `make test-xt` the flake skipped) | `13 passed`, `Successfully ran target test-xt for project mcp-hub`, exit 0 (`nx-test-xt.txt`) |
| Integration | `make test-int` | Run 1 (`docs/evidence/HIL-2/make-test-int.txt`, copy `make-test-int-run1-flake.txt`): `tools/tests_int` `12 passed`. **`orchestrator:test-int` `29 passed`**, including the 3 HIL-2 Mongo tests. Seven projects green. Three projects this diff does not change in their int paths failed on the memory-pressure flake: mcp-mongo `77 errors` (ServerSelectionTimeout / connection reset at setup), mcp-mocks `1 failed` (`_OperationCancelled` in a NOTIF-1 test), py-eventbus `1 failed` (`test_two_workers_never_process_the_same_job_at_once`: jobs re-run after their 6-thread lease expired under load). **Re-run of those three alone** (`nx run-many -t test-int -p mcp-mongo mcp-mocks py-eventbus --parallel=1`, `test-int-retry.txt`): `79 passed`, `11 passed`, `19 passed`, `Successfully ran target test-int for 3 projects`, exit 0. |
| Contracts | (no route or response model changed) | not run: the OpenAPI document is untouched |
| Plan | `python3 scripts/plan/plan.py check` | part of `make check` (passed: `plan ok`) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| HIL-1 (ADR 0020) | Approval records carry `status`, `requested_at`, `sla_hours`, `approver_id \| approver_role`; `notify.approval`'s recipients and message body; only the approver decides | yes. A real paused run's approval has `sla_hours: 48` and `requested_at` (int test). `_body` and `_recipients` are reused through public aliases. |
| RUN-2 (ADR 0018) | Workers restart, run side by side and drain on SIGTERM | yes. The sweep's state lives on the record; the sweep stops with the worker's stop and is bounded by `ORCHESTRATOR_DRAIN_SECONDS`. |
| EVT-1 | `publish(change=...)` runs the change and the event in one transaction, and a known key writes nothing | yes. Proved on the replica set by the rollback int test. |
| NOTIF-1 (ADR 0013) | In-app channel with `dedupe_key`; Telegram mock with buttons, `audience_user_id`, `ref` | yes |
| PII-5 (ADR 0023) | List orgs from `orgs`, tenant scope per org | yes (same pattern) |

## Mutation checks
`docs/evidence/HIL-2/mutate.py` → `docs/evidence/HIL-2/mutation-checks.txt`: **22 mutations, 22 killed, `source diff after all mutations: 0 files differ`**. The suites include the Mongo int test.
- **Due stages:** stamp ignored (M1); status ignored (M2); one threshold (M3); default SLA not from config (M11).
- **Stamp and event:** the compare-and-set without `pending` (M4) or without `field: None` (M5); a lost stamp that doesn't roll back (M6); a key that isn't fixed (M7); the stamp outside the transaction (M8); no stamp (M9).
- **Config:** `APPROVAL_SLA_CONFIG` ignored (M10).
- **Notifier:** dedupe off (M12); buttons on the escalation (M13); decided approvals chased (M14); escalation to the approver (M15); unstamped stage acted on (M16); per-event in-app dedupe (M17).
- **Review fixes:** first page only (M18); a duplicate stops the stages (M19); sweep deaf to stop (M20); unbounded shutdown (M21); HIL-1's dedupe taking a reminder for its message (M22).

First run: M5 and M11 survived, and each got a test (`test_a_stage_is_stamped_once_and_only_while_pending`, `test_the_default_sla_is_the_configured_one_not_a_constant`). M21 survived the second run because my code swallowed `CancelledError`. Fixed by suppressing only `TimeoutError`, with a timing assertion added.

## Review findings left open
Two independent reviews (general-purpose agents: code and security) of `git diff main...HEAD`. Neither found anything CRITICAL or HIGH. Fixed, each with a test:
- **MEDIUM (both reviews):** a backlog of 500+ old approvals not yet due starved newer ones. Now every page is read.
- **MEDIUM (security):** the consumer acted on unstamped stages, so a forged or early event was delivered. Now a stamp is required, and in-app dedupe is per approval, stage and user.
- **LOW:** a duplicate publish stopped the stages. Now re-stamped and continued.
- **LOW:** the sweep ignored the worker's stop and its final await was unbounded.
- **LOW:** HIL-1's first-message dedupe matched SLA messages.
- **LOW:** the old `serve` test passed through the sweep's error path; it now stubs the sweep.

Left open:
- **LOW · `apps/mcp-mocks/src/onb_mcp_mocks/notify/sla.py` `tell`:** the Telegram dedupe is check, then send, then store, which is not atomic. A job whose lease expires mid-delivery could send one stage twice. This is the same pattern as HIL-1's notifier; fixing it needs a reservation with a unique index (mcp-notify / SEC-1).
- **LOW · `libs/py-eventbus/src/onb_eventbus/bus.py:146`:** a publish rolled back by `_StageTakenError` (a race lost to a decision) marks its span ERROR. EVT-1's file; an allow-list of expected exceptions belongs there.
- **LOW · `tools/tests_xt/test_xt_consumers.py`:** the `notify.approval_sla` row checks that org B is untouched. The seeded org-A approval carries no `escalated_at`, so the handler now (correctly) sends nothing, and the row has no positive org-A read-back. Covering the reminder path or a foreign-id case needs a stamped approval in the shared xt seed (`world.py`/`seed_v1`), a shared file I left alone. The two-org sweep is proved with the in-memory store (`test_each_org_is_swept_in_its_own_scope`), not on Mongo.
- **LOW · `approval_sla.py` `run_once`:** the `orgs` scan and each org's candidate list are unbounded (all pending approvals held in memory per org). Fine at MVP scale; a `next_due_at` field and index would bound it.
- **LOW:** the escalation text names the approver's user id (an id, not PII).
- **Note:** `_STAMPS` in `notify/sla.py` repeats `onb_agents.approvals.sla.stage_field`, because mcp-mocks does not depend on onb_agents. M16 and the int test pin both.

## Left undone, and why
- `reminded_at` / `escalated_at` are not in `ApprovalRecord`/OpenAPI: no route of this goal needs them; UI-6 owns the inbox.
- No orchestrator compose service exists yet (DEP-1), so the sweep is proved in-process against the compose Mongo, like HIL-1's latency test.
- `/goal` could not be set from this orchestrated session; I worked to the text `plan.py goal HIL-2` printed.

## Found for other goals
- **DEP-1:** the orchestrator's Mongo user needs `find` on `orgs`. Ship `config/approval_sla.yaml` (or set `APPROVAL_SLA_CONFIG`) with the worker image, and mcp-notify runs `notify.approval_sla` (already in `consumer_handlers`). The worker's working directory must be the repo root for the default path.
- **UI-6:** show "overdue / escalated" from `reminded_at` / `escalated_at`; HR sees `approval.escalated` notes.
- **SEC-1 / mcp-notify:** an atomic reservation for Telegram sends (both notifiers).
- **EVT-1:** expected exceptions in `publish` should not mark spans as errors.
- **T-3 (xt seed):** a stamped approval in each org would let SLA rows assert positively.

## Decisions forced (ADRs written)
- `docs/adr/0027-approval-sla-reminder-and-escalation.md` (number reserved by the orchestrator). Shared-file changes, all additive:
  - `onb_contracts.notify`: 2 constants, 1 subscription entry.
  - `run_bus` SUBSCRIPTIONS: 1 entry.
  - `ApprovalStore`: an index, `awaiting_sla`, `mark_stage`.
  - `notify/approvals.py`: 2 public aliases, plus the `sla_stage` filter in the first-message dedupe.
  - `notify/main.py`: `consumer_handlers`.
  - `worker.py`: `Settings.approval_sla`, `_with_sla_sweep`.
  - xt: 1 consumer row.

  Exact-list tests were extended, never loosened: `test_run_store.py`, `test_notify_contracts.py`, `test_notify_main.py`. `test_worker_main.py`'s serve test got a `FakeStore.tenant` and a stubbed sweep; its assertions are unchanged.

## Traps hit
- Approval `_id`s are unique across the whole collection, not per org; tests that open the same (run, task) in two orgs collide.
- The worktree sandbox refuses heredocs and `. ./.env`: use Edit, script files and `uv run --env-file .env`.
- The rtk hook rewrites `make check` and truncates its output into the evidence file: use `rtk proxy make …`.
- `test-xt` setup flakes under three parallel stacks: re-run the errored ids and record both runs.
