# Checkpoint — HIL-1 · Pause for approvals and resume on the first decision

Branch `goal/hil-1-approvals-hil` (from main 6ce846f). Orchestrated session: hold at `in review`, never push,
no rebase/merge. Bedrock (OW-1) is not available: box 1 runs on the fake connector only.

## Done so far (all committed, unit-tested)
- `onb_contracts.approvals` + approval events/consumers in `onb_contracts.notify` (both subscription maps).
- `onb_agents.approvals`: `ApprovalStore`, `ApprovalSpec`, `resolve_approver`, `ApprovalDecider` (CAS + audit +
  `approval.decided` in one transaction), `RunStore.acquire_resume/release_resume`, `waiting_approval → running`.
- Orchestrator: `RunExecutor` opens approvals / parks tasks / `settle`; graph node `interrupt()`; consumers
  `resume_run`, `record_decision`; `AgentTaskExecutor` (packet, needs_human, requires_approval denial → approval).
- mcp-mocks `notify.approval` consumer (in-app + Telegram mock buttons, `audience_user_id` = approver).
- API `GET /approvals`, `POST /approvals/{id}/decision` (409 on the second), role matrix, contracts regenerated.
- ADR 0020.

## Next
1. `make test-xt` (rows added for both routes and the three consumers) — run on stack `ob-hil-1`.
2. Integration tests on real Mongo (`apps/orchestrator/tests_int`): pause/resume through the real bus, concurrent
   decisions, Telegram press end to end.
3. Evals `ap-001`…`ap-003` through the fake connector (harness + grading proof), latency measurement (30+
   decisions on compose) → `docs/evidence/HIL-1/`.
4. Mutation checks, code + security review, gates (`make check`, `test-int`, `test-xt`, `contracts-check`,
   `evals-selftest`), handoff, verifier. Then board: `on hold` (waiting on OW-1) if box 1 is the only failing box.

## Surprises
- py-agents and orchestrator unit tests cannot run in one pytest call (known).
- A worker only runs consumers its own bus subscribes: the new consumers are in both maps.
- Seed approvals (eval fixtures) lack `kind`/`title`/`hire_id`: the API reader defaults them.
