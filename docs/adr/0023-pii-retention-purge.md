# 0023 · How vault values are purged 90 days after onboarding completes

- **Status:** accepted · 2026-11-12 · goal PII-5
- **Context:** FR-P6 and PRD §7.10 Retention: vault values are purged 90 days after onboarding completes
  (ciphertext deleted, the record kept as `purged`), and a consent/purpose record is kept per hire. PII-2
  built `Vault.purge` for one token. Nothing decided when the 90 days start, what else of a value outlives
  the vault record, who the audit record names, or what runs the job.

## Decision

1. **The clock is the run's completion.** A run's completion time is the `at` of its last history entry
   with `to: completed` (RUN-1/RUN-2 write one on every move), falling back to `updated_at`. A hire is due
   when its newest completed run finished at least 90 days ago (`>=`: on day 90 the job purges, on day 89 a
   value still resolves) **and** it has no run still moving (`started`, `running`, `paused`, `blocked`). A
   failed or cancelled run never starts the clock. A second run of the same hire restarts it.
2. **Everything of the hire goes, not only the active value.** `Vault.purge_subject` purges every record of
   the subject in both partitions, superseded versions included: an old mobile number is as sensitive as
   the current one. Each record is verified before it is purged, so a tampered record raises and is left
   alone. The consent/purpose record (`pii_consent`) is never written by the purge.
3. **The hire document stops hinting at the values.** Masks become `[purged]`; the blind indexes
   (`email_hash`, `mobile_hash`, `pan_hash`, `aadhaar_hash`) and `pii_versions` are removed (a blind index
   would still let anyone with the org key confirm a guessed value). The tokens stay, so a token resolves
   to `[purged]`. `pii_purged_at` marks the hire done. HIRE-1 asked for exactly this clearing.
4. **Audit: one `pii.purge` record per purged value**, target `pii:<token>`, actor
   `agent:job:pii_retention`, reason `retention: 90 days after the onboarding run completed`. A failure for
   a hire is a `failed` record on `hire:<id>`. The audit action types only know users and agents
   (`ActorType`), so the job is an *agent*-typed actor with a `job:` id instead of a new actor type (a
   shared-module change this goal does not own; SEC-2 may add a `system` type).
5. **Order makes a crash safe.** Vault purge, then the audit records (each skipped if its success record
   exists), then the `pii_purged_at` mark. A crash leaves the hire unmarked, so the next pass finishes the
   work and audits each value exactly once. A marked hire is skipped, so a repeat pass changes and audits
   nothing.
6. **A separate process on a schedule.** `python -m onb_api.retention [--once]` runs a pass every
   `PII_PURGE_INTERVAL_SECONDS` (default 3600) until SIGINT/SIGTERM. It is not part of the API process:
   only `apps/api` may hold a `Vault`, and a purge must not depend on request traffic. It lists orgs from
   `orgs` and sets the tenant scope per org; every other read and write goes through `TenantRepository`.
   Tests drive it with a fake clock and a fake sleep.

## Consequences

- The job's Mongo user needs update on the vault collections and `new_hires`, find on `onboarding_runs`
  and `orgs`, and insert and find on `audit_log` (DEP-1).
- **Backups.** Purging deletes the ciphertext in the live database only. A backup taken before day 90
  still holds it, and the org's data key still opens it. Crypto-shredding (dropping the key version) is the
  only way to make backups unreadable, and it would also destroy values of hires still inside their 90
  days; PII-2's open question stays with SEC-1 and DEP-1. Until then, backup retention must be 90 days or
  less, or the purge is not complete in the backups.
- Encrypted document scans (HIRE-2) are not vault values and are **not** purged by this job; whether they
  follow the same 90 days is a PRD question (recorded in the handoff).
- One pass reads every completed run of an org; at MVP scale this is fine, and an index on
  `{org_id, status}` would bound it later.
