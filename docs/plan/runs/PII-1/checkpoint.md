# Checkpoint — PII-1 · per-org keys        branch goal/pii-1-per-org-keys

## Done
1. Tests first (red, `docs/evidence/PII-1/red-unit-tests.txt`): keys, envelope, keyring, key store,
   caplog, runbook. Deps added to py-crypto: `cryptography>=46,<47`, `onb-data` (uv.lock updated).
2. Implementation: `keys.py`, `envelope.py`, `key_store.py`, `keyring.py`, `rotation.py`, `errors.py`;
   `docs/runbooks/key-rotation.md`. 136 unit tests pass, coverage 99%.
3. `libs/py-crypto/tests_int` + `test-int` target: two orgs and both runbook procedures on compose Mongo.
   `make test-int` exit 0 (`docs/evidence/PII-1/make-test-int.txt`).
4. Mutation checks M1–M11 all killed (`docs/evidence/PII-1/mutation-checks.txt`).
5. `make check` passed (`docs/evidence/PII-1/make-check.txt`).

## Next (done: reviews folded in, gates re-run, handoff written)
- Fold in code-review and security-review findings, re-run gates, write handoff, run goal-verifier.
- Set board to `in review` and stop (orchestrator holds the merge). `make down` at the end.

## Surprises
- The session's rtk hook truncates `make` output; capture evidence with `rtk proxy make …`.
- Heredoc python inside Bash is sometimes refused by the worktree guard; scripts go in the job tmp dir.
