# Checkpoint — T-3 · cross-tenant suite

**Branch:** goal/t-3-cross-tenant-suite

## Steps done
1. Suite `tools/tests_xt/` (two seeded orgs on the compose Mongo; route, MCP tool and consumer
   cases; coverage checks; counts test; safety-net guard). `make test-xt` added and made a
   prerequisite of `make check`. `tools/tests/test_safety_net_suites.py` now also checks root suites.
2. Box 2 probe: a dummy route in `create_app` made `make test-xt` fail
   (`docs/evidence/T-3/dummy-route-run.txt`), then reverted.
3. Mutation checks M1–M8 all killed (`docs/evidence/T-3/mutations.txt`, script `mutate.py`).

## Next
- `make check` (now includes test-xt), code + security review, handoff, goal-verifier.

## Surprises
- `AuditLog` counts through `AppendOnlyRepository.count`, not `TenantRepository.count`: a mutation
  of the latter does not touch `/audit` totals, so M8 mutates the append-only path separately.
- The `rtk` hook truncates long `make` output written to a file; evidence runs use `rtk proxy make`.
- No production event consumer exists yet: `PLATFORM_SUBSCRIPTIONS` is empty; the generic consumer
  check runs on the suite's own `xt.probe` consumer until RUN-1 adds the first real one.
