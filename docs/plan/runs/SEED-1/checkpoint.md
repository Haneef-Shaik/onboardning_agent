# Checkpoint — SEED-1 · goal/seed-1-make-seed

## Steps done
1. `config/seed.yaml` + `onb_api.seed.spec` (passwords from `SEED_*_PASSWORD` env only) — `tests/test_seed_spec.py`.
2. `onb_api.seed.seeder` (create-only, idempotent: org, org keys, users via `MongoUserStore.add_user`,
   registry via `AgentRegistry.sync`, template via `TemplateLifecycle` publish by the HR_ADMIN) — `tests/test_seeder.py`.
3. `python -m onb_api.seed`, `scripts/seed.sh` (loopback tunnel when `MONGO_URI` is empty), `make seed`,
   `.env.example` entries — `tests/test_seed_command.py`, `tests_int/test_mongo_seed.py`.
4. Evidence: `docs/evidence/SEED-1/make-seed.txt` (two real runs on an emptied stack), `box3-git-grep.{sh,txt}`.

## Next
Mutation checks, ADR 0016, reviews, gates (make check, test-int, test-xt), handoff, verifier.

## Surprises
- The seeder implementation was written before its unit tests in step 2 (the spec in step 1 was red
  first). The mutation checks are the evidence that each guard has a named failing test.
- The `rtk` shell hook truncates long `make` output: capture with `rtk proxy make …`.
- The memory store has no client, so transactions (registry sync, publish) use the in-memory registry
  and template stores in unit tests; the real transactions run in `tests_int`.
