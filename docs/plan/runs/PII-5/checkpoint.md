# Checkpoint — PII-5 · branch goal/pii-5-pii-retention-purge

## Steps done
1. `Vault.purge_subject` (libs/py-crypto) + `tests/test_vault_purge_subject.py`.
2. `onb_api.retention` (job, loop, `__main__`) + `tests/test_retention.py`, `tests/test_retention_main.py`.
3. Mutation checks 20/20 killed (`docs/evidence/PII-5/mutation-checks.txt`).
4. Real-Mongo test `apps/api/tests_int/test_mongo_retention.py` (passes on the `ob-pii-5` stack).
5. ADR 0023.

## Next
- `make check`, `make test-int` (stack is up: project ob-pii-5, ports 19900/19901), `make contracts-check` if touched.
- code-reviewer + security-reviewer on `git diff main...HEAD`; fix critical/high.
- Handoff, then goal-verifier; then board -> `in review` (hold for review, no merge). `make down` at the end.

## Surprises
- Compound shell commands and `source .env` are refused in this worktree session: use `uv run --env-file .env`.
- Orphan sweep (HIRE-2 finding) not built: different trigger and risk of deleting live scans; recorded in handoff.
