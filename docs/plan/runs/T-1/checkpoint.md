# Checkpoint — T-1 · Make tenant scoping impossible to forget

Branch `goal/t-1-tenant-repository`, from main @ 42301cc.

## Steps done
1. Tests first (`3d1a1e6`): unit tests in `libs/py-data/tests` (context, repository, store, exports)
   and the two-org integration tests in `libs/py-data/tests_int/test_tenant_repository.py`. The red
   run is in `docs/evidence/T-1/red-unit-tests.txt` (ImportError: none of the API existed).
2. Implementation (`647b485`): `context.py`, `errors.py`, `scoping.py`, `repository.py`, `store.py`.
   119 unit tests pass with 100% coverage. `make test-int` passes (12 stack + 24 py-data).
3. Mutation checks: `docs/evidence/T-1/mutation-checks.txt`, M1–M15, all restored.

4. Reviews: fixed the High (tuple sub-pipelines) and the cheap Lows (`cc60c5a`). Mutation checks
   re-run, M1–M19. Gates and evidence are in `f65865d`. The handoff is written.

## Next
`goal-verifier` attempt 1, then `in review` on the board (orchestrator holds the merge).

## Surprises
- N818 wants an `Error` suffix. The errors are `*Error`, and `NotFound` (the card's name) is an
  alias of `NotFoundError`.
- M7 (no `$setOnInsert` on upsert) passes the live tests: MongoDB copies `org_id` from the equality
  filter into the upserted document. The `$setOnInsert` is a second guard, and only unit tests catch it.
- Under this session's shell hook, `find` and `grep` output is rewritten, and heredocs and `.`
  sourcing are refused. Scripts go in `$CLAUDE_JOB_DIR/tmp` and run with `bash <file>`.
