# Handoff — PII-5 · Purge sensitive values 90 days after onboarding completes        attempt 1 · goal/pii-5-pii-retention-purge · see `git log -1`

**Outcome claimed.** `python -m onb_api.retention` runs a scheduled pass that purges every vault record of a
hire 90 days after its newest run completed (ciphertext deleted, signed record kept as `purged`, consent/purpose
record untouched), audits each purged value, and does nothing on a second pass.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| With a fake clock: on day 89 values resolve; on day 90 the job purges and tokens resolve to `[purged]` (test) | yes | `uv run --frozen pytest apps/api/tests/test_retention.py`: `test_on_day_89_values_resolve_and_nothing_is_purged`, `test_on_day_90_the_job_purges_and_tokens_resolve_to_purged`, `test_the_same_clock_flips_from_resolving_to_purged_between_day_89_and_day_90` (day 89 + 23:59:59 vs day 90). Real Mongo: `apps/api/tests_int/test_mongo_retention.py` (`1 passed`, day 89 resolves, day 90 `[purged]`, ciphertext and nonce empty, read back with the root client). Fake clock and fake sleep: nothing waits. |
| The consent/purpose record survives the purge (test) | yes | `test_the_consent_purpose_record_survives_the_purge` (record equal before, after day 90 and after day 500); `libs/py-crypto/tests/test_vault_purge_subject.py::test_purge_subject_keeps_the_consent_record`; Mongo test reads `pii_consent` back (`purpose` intact). Mutant P9 (consent deleted) killed. |
| Each purge is audited and the job is idempotent (tests) | yes | Audit: `test_each_purged_value_is_audited_with_its_token_and_no_value` (3 `pii.purge` records, `pii:<token>`, no value), `test_a_partly_purged_hire_still_has_its_purged_values_audited`. Idempotent: `test_running_the_job_again_changes_nothing_and_audits_nothing_new`, `test_a_crash_after_the_vault_purge_is_finished_and_audited_once_by_the_next_run`, `test_a_crash_between_two_audit_writes_does_not_audit_the_first_value_twice`, `purge_subject` idempotent test, and the Mongo test's second pass (`0, 0, 0`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `Vault.purge_subject(org_id, subject_id=)` purges every record of the subject in both partitions, superseded versions included, verifies each before purging, leaves consent alone, is idempotent. One bad record raises `PurgeIncompleteError` (a `VaultIntegrityError`; `.outcomes`, `.errors`) after the rest are purged. | `libs/py-crypto/tests/test_vault_purge_subject.py` (8 tests) |
| After the purge a hire keeps its tokens; `pii_masked.*` = `[purged]`; `pii_versions` and `email_hash`/`mobile_hash`/`pan_hash`/`aadhaar_hash` are removed; `pii_purged_at` is set. | `test_the_hire_keeps_its_tokens_but_loses_masks_blind_indexes_and_versions`; Mongo test |
| Audit: action `pii.purge`, actor `agent:job:pii_retention`, target `pii:<token>` (success) or `hire:<id>` (failed). | `test_each_purged_value_is_audited…`, tampered-record test |
| A hire onboarded again (a newer completed run) is purged again after that run's 90 days; a hire with a run still moving is never purged. | `test_a_hire_re_onboarded_after_a_purge_is_purged_again_after_its_new_run`, `test_a_hire_with_another_run_still_active_is_not_purged` |
| Failed/cancelled/running runs never start the clock. | `test_a_run_that_did_not_complete_never_starts_the_clock[*]` |
| One org's or one hire's failure is counted and does not stop the pass; a stop request ends the wait. | `test_a_bad_org_record_fails_that_org_only…`, `test_a_tampered_record_fails_that_hire…`, `test_a_stop_request_ends_the_wait_between_passes` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| none listed for PII-5 by `plan.py show` | compose, 2026-11-12 | contributes evidence to NF-PII-INT @ MS-6 (retention) | Mongo test above | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | **Not green as a whole.** Every target up to `test-xt` passed (plan, env, tooling, lint, typecheck, unit tests). `test-xt` ended `Error 1` in 4 runs with only Mongo setup errors (`ServerSelectionTimeoutError … connection closed`, 2-6 different ids each run, 310-314 passed): the known flake, in cases this diff does not touch. Details in `docs/evidence/PII-5/make-check.txt`. Needs a re-run on a quieter machine. |
| Integration | `make test-int` | exit=0 (`docs/evidence/PII-5/make-test-int.txt`; the api suite includes `test_mongo_retention.py`). Stack `ob-pii-5`, Caddy 19900/19901. |
| Unit | `uv run --frozen pytest apps/api/tests/test_retention.py apps/api/tests/test_retention_main.py libs/py-crypto/tests` | all passed (475) |
| Mutation | `uv run --frozen python docs/evidence/PII-5/mutate.py` | 25/25 killed, `source diff after all mutations: 0 files differ`, `survivors: none` (`mutation-checks.txt`) |

`make test-xt`, `contracts-check` and evals do not apply: no route, MCP tool, event consumer or contract was added or changed.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| PII-2 | `Vault.purge` deletes ciphertext/nonce, keeps a signed `purged` record, idempotent; only `apps/api` and `libs/py-crypto` import `Vault` | yes. The job lives in `apps/api`; `test_vault_boundary` still passes. |
| RUN-2 | A run's history records `to: completed` with `at` | yes (used; the fallback is `updated_at`) |
| EVT-1 | Not used: the job reads run state directly each pass, so it needs no event subscription and `run_bus` is untouched. | n/a |
| HIRE-1 | Hire document layout (`pii`, `pii_masked`, `pii_versions`, blind-index fields) | yes. Cleared as HIRE-1's note asked. |

## Mutation checks
`docs/evidence/PII-5/mutation-checks.txt` (25 mutants, all KILLED, source restored): P1 `>=`→`>` (day-90 test); P2 two days early and P3 30 days (day-89 tests); P4 clock from `updated_at`; P5 active run ignored; P6 non-completed runs start the clock; P7 superseded versions kept; P8 Aadhaar partition skipped; P9 consent deleted; P10 no audit; P11 purged hire re-counted; P12 audit duplicated after a crash; P13 hire never marked; P14 mask kept; P15 blind index kept; P16 a failure stops the pass; P17 failure not audited; P18 loop dies on a failing pass; P19 loop runs once; P20 only the first org; P21 one org's failure aborts the rest; P22 one bad record stops the hire's purge; P23 partial purge not audited; P24 mark never expires; P25 stop does not end the wait.

## Review findings left open
Reviewed by a code and security review agent over `git diff main...HEAD`: no critical or high. Medium findings 1-4 (org failure aborting the pass; one bad record stopping the hire's purge; stop not interrupting the sleep; mark skipping a re-onboarded hire) were fixed test-first (P21-P25). Left open:
- low · `job.py` `_audit_failure` · a persistent failure (a tampered record) writes one `failed` audit record per pass (hourly by default); no dedupe. Left: dedupe by day would need state; SEC-1 can decide.
- low · `job.py` `_purge_hire` · after a crash, the retry reports `values_purged` 0 for values purged before the crash (counts newly purged only). Audit records are correct.
- low · `job.py` `_clear_hire` · the update result is not checked; a hire deleted mid-pass is still reported purged.
- low · a run started in the instant between the active-run check and the purge (TOCTOU) could have fresh values purged; unlikely at MVP scale. HR editing PII of an already purged hire is not blocked (the new values get purged only after a newer completed run).

## Left undone, and why
- **HIRE-2 orphan sweep not built (recorded here).** A crash between storing an encrypted scan (`documents.files`/`documents.chunks`) and writing `documents_meta` leaves orphaned ciphertext. It does not fit this job: its trigger is not the 90 days, it needs a grace window against in-flight uploads, and a bug would delete live scans. It needs its own small goal (list `documents.files` older than a grace period with no `documents_meta` row, delete via `DocumentBucket.delete`).
- Encrypted scans are not purged at 90 days (not vault values, PRD §7.10 Retention names the vault). PRD question for Haneef: should scans follow the same 90 days?
- Deploy: no compose service or `make` target runs the job yet (DEP-1): `python -m onb_api.retention` with `MONGO_URI`, `MONGO_DATABASE`, `PII_MASTER_KEY`, optional `PII_PURGE_INTERVAL_SECONDS`. Its Mongo user needs update on the vault collections and `new_hires`, find on `onboarding_runs` and `orgs`, insert and find on `audit_log`.

## Found for other goals
- SEC-1/DEP-1: purge does not reach backups; crypto-shredding vs backup retention ≤ 90 days (ADR 0023).
- SEC-2: `ActorType` has no `system`; the job is an agent-typed actor `job:pii_retention`.
- UI/HIRE: a purged hire shows `[purged]` masks; `reveal` of a purged field returns `[purged]` (the vault answers it).
- Indexes: a pass reads all completed runs per org; an `{org_id, status}` index would bound it.

## Decisions forced (ADRs written)
`docs/adr/0023-pii-retention-purge.md`.

## Traps hit
- Compound shell commands and `source .env` are refused in this worktree session; used `uv run --env-file .env`.
- The in-memory database enforces `_id` uniqueness across orgs, so test runs need distinct ids per org.
- Changing the mutation target lines (two audit loops) broke a mutant pattern; patterns must stay unique.
