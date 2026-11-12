# Checkpoint — HIL-2 · goal/hil-2-approval-sla

## Steps done
1. `onb_agents.approvals.sla`: `SlaPolicy`, `load_sla_policy`, `due_stages`; `config/approval_sla.yaml` (dcc26bd).
2. `approval.overdue` / `notify.approval_sla` contracts in both subscription maps; `ApprovalStore.awaiting_sla`,
   `mark_stage`; `onb_orchestrator.approval_sla.ApprovalSlaJob` + `run_sla_forever` (895ec95).
3. Worker: `Settings.approval_sla` from `APPROVAL_SLA_CONFIG`; `serve` runs the sweep beside the bus worker (78da08b).
4. mcp-notify: `notify.approval_sla` consumer (`notify/sla.py`), `consumer_handlers` in `notify.main` (6a38dd0).
5. xt row; Mongo integration test (restart, rollback, decision first) (41b75c9).
6. Mutation checks 15/15 (`docs/evidence/HIL-2/mutation-checks.txt`); ADR 0027.

## Next
Gates (`make check`, `make test-int`), reviews, handoff, verifier, board → in review (hold for orchestrator).

## Surprises
- Approval `_id`s are collection-wide (not per org): tests need distinct run ids across orgs.
- The worktree sandbox refuses heredocs and `. ./.env`: use Edit, script files, and `uv run --env-file .env`.
