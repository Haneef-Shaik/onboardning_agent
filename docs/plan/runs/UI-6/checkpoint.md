# Checkpoint — UI-6

Branch `goal/ui-6-approvals-ui` (from main 5f61a29).

## Done
1. API `POST /mock-outbox/{message_id}/press` (audience check, 409 pre-check, in-process relay to
   `POST /webhooks/telegram`), `audience_user_id` on outbox messages, role matrix + xt rows, contracts regenerated.
2. Web: `apps/web/src/approvals/` (inbox, outbox, api) + `/approvals/*` route in App.tsx; vitest 12 new.
3. E2E: `apps/web/e2e/approval_seed.py` + `approvals.spec.ts` (3 boxes) — 23/23 e2e green.

## Next
ADR 0026, mutation checks, reviews, gates (`make check`, `make test-xt` on the ob-ui-6 stack), handoff, verifier.

## Surprises
- e2e: the runner reads the hire's `manager_id` from `new_hires` in the runs' database; the e2e hire service has its
  own memory store, so the harness mirrors `{_id, manager_id}` (else the approver falls back to the HR role).
- `apps/web/e2e/*.py` is not under the ruff gate (main's `run_seed.py` has an import-order warning).
