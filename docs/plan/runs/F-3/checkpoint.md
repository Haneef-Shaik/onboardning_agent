# Checkpoint — F-3 · Gate every change in CI

**Branch:** goal/f-3-ci-gate · **Updated:** 2026-11-10

## Steps done
1. Guard test `tools/tests/test_ci_workflow.py` written first. RED: 20 failed (no workflow, no ADR).
2. `.github/workflows/ci.yml` (check → integration) and `docs/adr/0001-ci-host.md` (GitHub Actions). GREEN: 20 passed.
3. Mutation checks M1–M10 all caught (`docs/evidence/F-3/mutation-checks.txt`). M8 first survived, and the guard now parses the ADR's protection payload.
4. actionlint 1.7.12 is clean. The local red/green rehearsal of the check job is recorded.
5. DEC-5 ledger row set to pass.

## Next step
goal-verifier (attempt 1), then the board set to `in review` (orchestrator hold). Review is done and its findings are fixed.

## Surprises
- No git remote, and the orchestrator says never push. Boxes 2 and 3 need hosted run links, so they stop at "workflow ready", as the card's scope allows.
- `docs/adr/0001-*` was already taken by F-1. The card's path was kept anyway.
- `make test-int` has no targets on main yet (F-2 adds them).
- Local Docker daemon is not running.

## Verifier
- Attempt 1 (2026-11-10): **fail**. Box 1 met; boxes 2 and 3 unmet (no remote, so no hosted run links); DEC-5, gates and rules ok. Retry guidance: no code changes; owner action (create remote, push, protect main, record red/green and integration run links after F-2), then re-verify. Goal set to `on hold`.
