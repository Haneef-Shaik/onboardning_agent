# 0027 · Approval SLA: a sweep stamps each stage with its event, once; HR is told, the approver decides

- **Status:** accepted · 2026-11-13 · goal HIL-2
- **Context:** PRD FR-H4: "Approvals have an SLA; overdue approvals remind the approver, then escalate to HR."
  HIL-1 (ADR 0020) stores `requested_at` and `sla_hours` (the template task's, or none for tool-call and
  `needs_human` approvals) on each approval. The bus (EVT-1) has no delayed delivery, workers restart and can
  run side by side (RUN-2, ADR 0018), and the card asks that thresholds come from config and that a restart
  never repeats a reminder. The PRD names no thresholds and no channel for HR.

## Decision

1. **Thresholds are multiples of the approval's SLA, from `config/approval_sla.yaml`.** `remind_after_sla`
   (1.0: the moment it is overdue) and `escalate_after_sla` (2.0), counted from `requested_at`;
   `default_sla_hours` (24) for an approval without `sla_hours`; `escalate_to_role` (`HR` or `HR_ADMIN`);
   `check_interval_seconds` (60). `APPROVAL_SLA_CONFIG` names another file. Missing keys take the defaults (which
   equal the shipped file); an unknown key, a non-positive or non-finite number, an escalation not after the
   reminder, or another role stops the worker at startup (`ConfigError`).
2. **A periodic sweep in the orchestrator worker.** `onb_orchestrator.approval_sla.ApprovalSlaJob` runs beside the
   bus worker (`serve`) every `check_interval_seconds` and stops with it. It lists orgs from `orgs` and sets the
   tenant scope per org (the PII-5 pattern, ADR 0023); every read and write goes through `TenantRepository`. Per
   org it reads pending, not yet escalated approvals, oldest first, up to 500 per pass.
3. **Each stage is stamped and published together, once.** For each stage `due_stages` owes (reminder before
   escalation), the sweep publishes `approval.overdue {approval_id, run_id, task_id, stage, escalate_to}` under the
   fixed key `approval:<id>:<stage>`, with the stamp (`reminded_at` / `escalated_at`) as the publish's `change`:
   one outbox transaction. The stamp is a compare-and-set that applies only while the approval is `pending` and
   the stage unstamped; when it fails (a decision landed, another worker stamped first) the transaction rolls back
   and nothing is sent. So the record, not the worker's memory, stops a repeat after a restart; the fixed key is a
   second stop; a crash before the commit leaves neither stamp nor event, and the next pass sends it.
4. **The notifier (`notify.approval_sla`, mcp-notify) delivers.** A reminder goes to the approval's approver (or
   its role's users) in-app (`approval.reminder`) and on the Telegram mock with the same Approve/Reject buttons as
   the first message. An escalation goes to every `escalate_to` user in-app (`approval.escalated`) and on the
   Telegram mock **without buttons**: HR is told whom the approval waits on; only the approver decides (HIL-1's
   rule is unchanged). Each part is deduplicated on its own (in-app by event and user; the message by approval,
   user and `sla_stage`), so a redelivery tells nobody twice and a half-done job finishes. Nothing is sent for an
   approval decided or cancelled meanwhile.
5. **Escalation does not reassign or decide.** The approval stays `pending` with its approver; HR can follow up
   or, through RUN-2's controls, complete the task by hand.

## Consequences

- Two new approval fields (`reminded_at`, `escalated_at`), not yet in `ApprovalRecord` or the OpenAPI document: the
  inbox (UI-6) can add them when it wants an "overdue" badge.
- One new index on `approvals`: `{org_id, status, escalated_at, requested_at}`.
- A new event and consumer: `approval.overdue` → `notify.approval_sla`, in both subscription maps (`run_bus` and
  `NOTIFY_SUBSCRIPTIONS`) and the cross-tenant suite.
- The orchestrator's Mongo user also needs find on `orgs` (DEP-1); mcp-notify runs the new consumer with no new
  settings.
- At most one reminder per approval: FR-H4 says "remind, then escalate"; a repeating reminder would need a new
  stage and a new key per repeat.
