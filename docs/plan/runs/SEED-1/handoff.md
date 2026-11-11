# Handoff — SEED-1 · Seed an org, users and templates with one command        attempt 1 · goal/seed-1-make-seed · see `git log -1` (code at 8bffcb0)

**Outcome claimed.** `make seed` creates the org `org_demo` (with its PII-1 keys), an HR_ADMIN, an HR
user, two managers (through AUTH-1's `MongoUserStore.add_user`), the agent registry and tool catalog
(REG-1's `AgentRegistry.sync`) and the published `engineer-india` template (TPL-2's lifecycle,
published and audited as the HR_ADMIN). A second run changes nothing. Passwords come only from
`SEED_*_PASSWORD` in the git-ignored `.env`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| On an empty database, `make seed` creates every listed record (test or recorded output) | yes | **Test:** `apps/api/tests_int/test_mongo_seed.py::test_make_seed_on_an_empty_database_creates_every_listed_record` runs the command's `main` (what `scripts/seed.sh` runs) on an empty scratch database and reads back with the root client: `orgs`, `org_keys` v1, 4 `users` (HR_ADMIN, HR, 2 MANAGER, `$2b$12$` hashes) + 4 `user_logins`, 6 active `agent_registry` v1, `tool_catalog`, `templates` head v1, `template_versions` v1 published by `u_demo_hradmin`, the draft locked, audit `template.publish` success + 6 `registry.agent_register`. `::test_the_seeded_users_log_in_with_their_passwords_from_the_environment` logs each user in through `POST /auth/login` and `/me`. `make test-int` → see Gates. **Recorded output:** `docs/evidence/SEED-1/make-seed.txt` (real `make seed` after `docker compose -p ob-seed-1 down -v`: `14 created, 0 unchanged`). Unit: `apps/api/tests/test_seeder.py::test_an_empty_database_gets_the_org_its_keys_the_users_the_registry_and_the_template`. |
| A second run changes nothing (test) | yes | `apps/api/tests_int/test_mongo_seed.py::test_a_second_make_seed_changes_nothing`: snapshot of every collection (all documents and indexes) before and after a second run is equal; every line `unchanged`. Unit: `test_seeder.py::test_a_second_run_changes_nothing` (database, registry, template store and audit log equal), `::test_a_second_run_writes_no_org_record`. Recorded: run 2 in `make-seed.txt` → `0 created, 14 unchanged`. |
| Seed passwords come from env; none are committed (`git grep` recorded) | yes | `docs/evidence/SEED-1/box3-git-grep.txt` (re-runnable `box3-git-grep.sh`): no `SEED_*_PASSWORD` is given a value in any tracked file (exit 1); mentions are names only (`.env.example` empty values, `config/seed.yaml` `password_env:`) plus throwaway test-only values in `apps/api/tests*`; no `password:` field in `config`/`templates`; the local `.env` values appear in no tracked file; `.env` is git-ignored and untracked. Tests: `test_seed_spec.py::test_the_shipped_seed_file_holds_no_password`, `::test_a_password_written_into_the_seed_file_is_refused`, `::test_a_missing_password_variable_stops_the_seed_and_names_it[*]`, `::test_a_password_variable_must_be_a_seed_password_name`, `test_seeder.py::test_no_password_is_stored_only_bcrypt_hashes`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `make seed` (after `make up`) seeds `config/seed.yaml`: org `org_demo` "Demo Org (synthetic)"; users `u_demo_hradmin` hr.admin@demo.example (HR_ADMIN), `u_demo_hr` hr@demo.example (HR), `u_demo_mgr_priya` priya.manager@demo.example and `u_demo_mgr_arjun` arjun.manager@demo.example (MANAGER); passwords from `SEED_HR_ADMIN_PASSWORD`, `SEED_HR_PASSWORD`, `SEED_MANAGER_PASSWORD`. | `test_mongo_seed.py`, `make-seed.txt` |
| The seeded users log in through `POST /auth/login` with those passwords; `/me` reports `org_demo` and the role. | `test_the_seeded_users_log_in_with_their_passwords_from_the_environment` |
| `engineer-india` v1 is published in `org_demo` by `u_demo_hradmin` (audited `template.publish`), and every agent in `config/agents` is registered v1. | box 1 test |
| The seed is create-only and idempotent: it never changes an existing user (password, role) or a template key that has a version. The registry step is REG-1's full sync (`config/agents` is the source of truth): edits print `updated agent:…`, removals `retired agent:…`. | `test_a_second_run_changes_nothing`, `test_an_existing_user_keeps_its_password`, `test_an_existing_template_is_left_as_it_is`, `test_registry_changes_are_reported_as_updates_and_retirements_not_creations` |
| `make seed` needs `MONGO_DATABASE`, `PII_MASTER_KEY` and the three `SEED_*_PASSWORD` in `.env` (documented in `.env.example`). `MONGO_URI` empty → a loopback socat tunnel to this compose project's mongo as root (label `onb.seed-tunnel=<network>`, removed on exit); set → used as given (VM). | `test_seed_command.py::test_a_missing_setting_is_refused_before_any_connection[*]`, `make-seed.txt` |
| **The API must run with the same `PII_MASTER_KEY` and `MONGO_DATABASE`** as the seed: org keys are wrapped by it, and a seed with another key is refused before any write (`seed refused: …`). | `test_a_seed_with_another_master_key_is_refused_and_writes_nothing`, `test_org_keys_made_under_another_master_key_stop_the_seed` |
| Programmatic use: `onb_api.seed.__main__.build_services(store, master_key, config_dir)` + `Seeder(services, bcrypt_rounds=…).run(load_seed(path, env))` returns a `SeedReport` (`steps`, `changed`, `unchanged`, `render()`). | `test_mongo_seed.py`, `test_seeder.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show SEED-1` lists no ledger rows; `docs/plan/ACCEPTANCE.md` does not name SEED-1 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check (incl. cross-tenant suite) | `make check` | pass on 8bffcb0: `make check: all passed` (lint, typecheck, test for 17 projects; tools/tests_xt 196 passed; mcp-hub test-xt) |
| Integration | `make test-int` | pass on 8bffcb0: exit 0 (tools/tests_int + `test-int` for 8 projects, incl. apps/api tests_int with test_mongo_seed.py) |
| Cross-tenant | `make test-xt` (part of `make check`) | pass on 8bffcb0: 196 passed (in make check) |
| Contracts | not run: no API model or route changed (`git diff main...HEAD -- libs/py-contracts apps/api/src/onb_api/*/routes.py` is empty) | n/a |
| Eval self-test | not run: no `libs/py-agents`, prompts or manifests changed | n/a |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| AUTH-1 | Users via `MongoUserStore.add_user` in `tenant_scope(org)`; `user_logins` stays in step; emails unique across orgs (`EmailTakenError`) | yes: login works for every seeded user (box 1 int test); a taken email is refused (`test_an_email_registered_to_another_user_stops_the_seed`) |
| PII-1 | `keyring.create(org)` once in `tenant_scope`; `ensure_org_keys_index`; `verify` unwraps every version and fails closed | yes: `org_keys` v1 written once; wrong master key → `KeyUnwrapError`, nothing written |
| REG-1 | `AgentRegistry.sync` refuses with no `orgs`; unchanged manifests write nothing; audited in every org | yes: seed inserts `orgs` first; second run writes nothing (snapshot equal) |
| TPL-2 | Publish via `TemplateLifecycle` (validated against the registry, atomic with its audit record); `CONFIG_DIR` holds catalog and sink policy | yes: v1 published and audited; a failed publish audit leaves no version and the rerun finishes (`test_a_run_that_failed_at_the_publish_…`) |
| T-2 | Mongo test-int suites run under the tenant safety net | yes: root read-backs wrapped in `unscoped(reason)` |

## Mutation checks
25/26 killed; the record with the failing test names is `docs/evidence/SEED-1/mutations.txt` (re-run with
`uv run --frozen python docs/evidence/SEED-1/mutate.py [M…]`). Highlights:
- M3 keys always created → `test_a_second_run_changes_nothing` and 4 more FAILED.
- M5 no look for an existing login → `test_a_second_run_changes_nothing` and 7 more FAILED.
- M9 stored non-HR_ADMIN may publish → `test_a_stored_publisher_who_is_not_hr_admin_publishes_nothing` FAILED.
- M10 no look for an existing template → `tests_int/test_mongo_seed.py::test_a_second_make_seed_changes_nothing` FAILED (and 3 unit tests).
- M13 inline `password:` ignored → `test_a_password_written_into_the_seed_file_is_refused` FAILED.
- M20 Mongo error text printed → `test_a_database_error_names_its_type_only` FAILED.
- M26 first catalog write reported as update → unit and test-int box-1 tests FAILED.
- **M12 survives, equivalent:** a published draft without its template head is unreachable (TPL-2 commits both in one transaction), so the "template exists" return always fires first.

## Review findings left open
- LOW · `apps/api/src/onb_api/seed/seeder.py` `_registry` · the registry sync is audited in every org in `orgs` as the seed's HR_ADMIN (REG-1 behaviour; ADR 0016 Consequences). Needs a `system` actor (REG-1/AUD-1 finding).
- LOW · `Makefile` `seed` · `.env` is read with `bash` (`set -a; . ./.env`), so `$`/backticks in an unquoted password are expanded (pre-existing pattern for every target). Documented in `.env.example`; a literal dotenv reader would remove it.
- INFO · the root `MONGO_URI` is in the seed process's environment on the dev machine (same-user readable only).
- INFO · `_pending_draft` publishes any unpublished draft with the seed template's exact body, whoever made it, as the HR_ADMIN (only before the key has a version; audited correctly).

## Left undone, and why
- No user-create API route (out of scope; ADR 0004's cross-org email decision is open with Haneef).
- The org and user creations are not audited: there is no actor before the first user exists (needs a `system` actor).

## Found for other goals
- **Haneef / ADR 0004:** cross-org unique emails only matter once a user-create route exists; SEED-1 adds none. Decide before any goal adds one.
- **DEP-1:** on the VM, set `MONGO_URI` (through the SSH tunnel) to a least-privilege user rather than root; keep `PII_MASTER_KEY` identical between the seed and the API; run `make seed` once after `make up`.
- **E2E-1 / MS-3:** log in as the seeded users (`hr.admin@demo.example` etc., passwords from `.env`); `engineer-india` v1 is published in `org_demo`.
- **AUD-1 follow-up / SEC-1:** a `system` actor would let the seed audit org/user creation and stop registry syncs naming one org's HR_ADMIN in other orgs' logs.
- **Compose/API goal:** `.env.example` now carries `MONGO_URI` (empty), `MONGO_DATABASE=onboarding` and `PII_MASTER_KEY`; reuse them when the API joins compose instead of adding duplicates.

## Decisions forced (ADRs written)
- `docs/adr/0016-make-seed-create-only.md`: seed file as data, create-only and idempotent (registry is the full-sync exception), conflicts stop the seed, the first HR_ADMIN is the actor, `MONGO_URI` or a loopback tunnel.

## Traps hit
- The `rtk` shell hook truncates long `make` output and rewrites `diff` (it printed "Files are identical" for files that differed). Use `rtk proxy make …` and `cmp` for evidence. This hid one regression (catalog v1 printed as `updated`) until `make test-int` caught it; fixed in 8bffcb0 with a unit assertion and M26.
- The in-memory `memory_store` has no client, so transactions only run in `tests_int`; unit tests use the in-memory registry and template stores.
- The seeder implementation (step 2) was written before its unit tests; the mutation checks show each guard has a named failing test.
