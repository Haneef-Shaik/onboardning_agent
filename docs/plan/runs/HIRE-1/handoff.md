# Handoff — HIRE-1 · Create hires with PII held as tokens, and reveal with a reason        attempt 1 · goal/hire-1-hires-reveal · see `git log -1` (handoff commit)

**Outcome claimed.** HR creates a hire whose sensitive values are validated (PII-3) and go straight
into the vault; `new_hires` keeps only tokens, stored masks and per-org blind indexes; HR/HR_ADMIN
reveal one field with a reason (Aadhaar: HR_ADMIN only), each reveal written to `pii_access_log` and
`audit_log` before the value leaves, and HR_ADMIN reads the log through `GET /pii-access-log`.

Routes: `POST /hires`, `GET /hires`, `GET /hires/{hire_id}`, `PUT /hires/{hire_id}/pii/{field}`,
`POST /hires/{hire_id}/pii/{field}/reveal`, `GET /pii-access-log`. Code: `apps/api/src/onb_api/hires/`,
contracts `libs/py-contracts/src/onb_contracts/hires.py`, wiring `app.py`/`main.py`, `partial=` in
`libs/py-data/src/onb_data/store.py`. Decisions: `docs/adr/0008-hire-pii-masks-keys-and-dedupe.md`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| After `POST /hires`, no raw fixture value appears in `new_hires` or in captured logs (scan test) | yes | Unit: `apps/api/tests/test_hires.py::test_after_post_hires_no_raw_value_is_in_new_hires_or_the_logs` (every collection outside the vault partitions, the audit log, and `caplog` at DEBUG; values as typed and canonical). Mongo: `apps/api/tests_int/test_mongo_hires.py::test_after_post_hires_no_raw_value_is_stored_outside_the_vault_ciphertext_or_logged` (root scan of every collection incl. the vault, PyMongo's DEBUG command log included). `make test-int` → see Gates. Mutant M16 killed. |
| MANAGER reveal → 403; HR reveal of Aadhaar → 403; HR_ADMIN Aadhaar reveal with reason → 200; missing reason → 422 (tests) | yes | `test_hires.py::test_a_manager_reveal_is_403`, `::test_an_hr_reveal_of_aadhaar_is_403_and_audited_as_denied`, `::test_hr_admin_reveals_aadhaar_with_a_reason`, `::test_a_reveal_without_a_reason_is_422[missing,empty,blank]`; on Mongo `test_mongo_hires.py::test_every_reveal_lands_in_pii_access_log_and_reads_back` (HR_ADMIN 200, HR 403). Command: `uv run pytest apps/api/tests -q` → `348 passed`. Mutants M3, M4, M5 killed. |
| Every reveal writes `pii_access_log`, readable through `GET /pii-access-log` (test) | yes | `test_hires.py::test_every_reveal_writes_pii_access_log_readable_by_hr_admin` (3 reveals → 3 entries, newest first, ids match the reveal answers), `::test_refused_reveals_write_no_access_entry`, `::test_no_value_is_released_when_the_access_log_write_fails`, `::test_the_access_log_lists_sink_resolutions_too` (PII-4's `kind: resolve` entries listed too); Mongo: `test_mongo_hires.py::test_every_reveal_lands_in_pii_access_log_and_reads_back`. Mutants M6, M7 killed. |
| A duplicate email in the same org is rejected; the same email in another org is accepted (test) | yes | `test_hires.py::test_a_duplicate_email_in_the_same_org_is_refused` (409, case/space variants), `::test_the_same_email_in_another_org_is_accepted`, `::test_the_unique_index_refuses_a_duplicate_that_slipped_past_the_check`; Mongo: `test_mongo_hires.py::test_the_email_index_is_unique_per_org_and_partial` (real `{org_id, email_hash}` unique partial index); test-xt `POST /hires` check (org B's email is free in org A). Mutants M8–M10, X2 killed. |
| `make test-xt` covers every new route | yes | `tools/tests_xt/test_xt_api.py::ROUTE_CASES` rows for all six routes (own checks + generic leak/steering checks per role); `test_xt_coverage.py::test_every_api_route_has_a_cross_tenant_case` passes; the run's surface listing prints the six routes. `make check` (runs test-xt) → see Gates. Mutant X1 killed. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `HireService.build(store, keyring, auditor=, users=)` is the hire API's service; `create_app(..., hires=)` mounts it; without it the routes answer 503. Startup makes hires, vault, org-keys and access-log indexes. `main.py` needs `PII_MASTER_KEY` (base64 of 32 bytes) and fails fast without it. | `test_hires.py::test_without_a_hire_service_the_routes_answer_503`, `test_auth_settings.py::test_startup_fails_without_the_pii_master_key`, `::test_startup_fails_with_a_malformed_pii_master_key` |
| A hire document: `{_id: "h_<24 hex>", org_id, name, role, location, start_date: "YYYY-MM-DD", manager_id, notes (redacted), pii: {field: ptk_…}, pii_masked: {field: mask}, pii_versions: {field: int}, email_hash, mobile_hash?, pan_hash?, aadhaar_hash?, id_proof: {aadhaar_consent, alternative_id}, created_by, created_at}`. No raw value anywhere in it. | scan tests above |
| Responses never carry a token or value for a hire, only masks (`[masked]` for IFSC, account-holder name and seeded tokens). Only the reveal returns a value, with `Cache-Control: no-store` and `expires_in_seconds: 30`. | `test_hr_creates_a_hire_and_sees_every_field_masked`, `test_hr_lists_the_orgs_hires_masked`, M13, M17 |
| `pii_access_log` entries: `{_id, org_id, kind: reveal|resolve, actor: {type: user|service, id}, subject: {type: new_hire, id}, field, token, masked, reason?, sink?, run_id?, task_id?, at}`; reasons are redacted. `GET /pii-access-log` (HR_ADMIN) filters `hire_id`, `field`, `kind`, pages newest first. | box 3 tests |
| Audit actions: `hire.create` (`hire:<id>`), `pii.update` (`hire:<id>/<field>`, SUCCESS before the vault write, FAILED if it then does not complete), `pii.reveal` (SUCCESS with redacted reason; DENIED for a non-admin Aadhaar reveal; FAILED when the vault refuses a token). | `test_an_update_is_audited_without_the_value`, `test_an_hr_reveal_of_aadhaar_is_403_and_audited_as_denied`, `test_hires_failures.py` |
| An update never strands a value: the email is reserved on the hire before the vault is touched, and the hire takes a token only if its vault version is newer than the one it holds (older racer → 409 retry). | `test_hires_failures.py` (5 tests), N1–N5 |
| `DataStore.ensure_index(..., partial={field: {"$exists": True}})` — only that form, never `org_id`. | `libs/py-data/tests/test_store.py::test_a_partial_*`, M19 |
| `onb_api.testing_mongo` (test-only): `MemoryDatabase`, `memory_store()`, `memory_keyring()` — a driver stand-in under the real `DataStore`, enforcing unique and partial indexes. | used by the hire unit tests (`test_hires.py`, `test_hires_failures.py`) |
| test-xt: `World.vault_hires()` seeds one vault-backed hire per org (values in `world.XT_VAULT_HIRES`, B's in `b_markers()`) on first use; the generic steering test builds a fresh request per call. | `make test-xt` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U3 @ MS-6 | compose, 2026-11-11 | contributes: a hire is created by HR through the API (template pick and run start are later goals) | box tests above | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check (incl. test-xt) | `make check` | exit 0 — `make check: all passed`; `NX Successfully ran target test for 17 projects` (api `348 passed`, py-contracts `202 passed`, coverage 99.45%); test-xt `127 passed in 64.28s` |
| Integration | `make test-int` | exit 0 — `NX Successfully ran target test-int for 7 projects` (api suite incl. `test_mongo_hires.py`) |
| Contracts | `make contracts-check` | exit 0 — `contracts-check: openapi.json and the TS client are up to date` |
| Cross-tenant | `make test-xt` (inside `make check`) | `cross-tenant surface: 12 API routes, 6 MCP tools, 0 event consumers` · `127 passed` (lists the six HIRE-1 routes) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| AUTH-1 | `require_role`, tenant scope from the token, `manager_filter`/`require_in_manager_scope` (404) | yes — manager tests, M11, M12 |
| AUD-1 | `AuditLog.audit` fail-closed; targets `kind:id`; no PII in target/reason | yes — reasons redacted; reveal/update fail closed on audit errors |
| PII-2 | `Vault.put/resolve/verify/purge`; `ensure_vault_indexes` at startup; catch `CryptoError` | yes — note: `put` supersedes immediately, hence the update ordering (see Traps) |
| PII-3 | validators before mask/blind index; Verhoeff | yes — M1, M2 |
| PII-4 | `pii_access_log` shape of sink entries | yes — listed by `GET /pii-access-log` |
| T-3 | ROUTE_CASES per route; foreign id == missing id | yes — six rows, X1 |
| T-2 | safety net in Mongo suites | yes — root read-backs in `unscoped(...)` |

## Mutation checks
32 mutants, 32 killed: `docs/evidence/HIRE-1/mutations.md` (runner `docs/evidence/HIRE-1/mutate.py`).

## Review findings left open
- LOW · `service.py` `_reserve_email`/`commit_pii` · a database error *between* the vault put and the hire commit (not a race, an outage) still leaves the old value superseded and the hire pointing at it; a retried PUT repairs it. A real fix needs vault support (put without supersede, or restore) — PII-2/PII-5.
- LOW · `service.py` reveal · the access-log entry is written before the success audit record; if the audit write then fails, the entry records a reveal whose value was not released (fails closed, over-reports).
- LOW · `service.py` reveal · a denied Aadhaar reveal is audited against the client-supplied hire id before lookup (kept so 403 says nothing about existence).
- LOW · `pii_access_log` is insert-only in the API but not an `onb_data` append-only collection (PII-4's sink writes it via `tenant()`); for SEC/AUD.
- LOW · the email blind index is computed twice per create (cost only).

## Left undone, and why
- `PII_MASTER_KEY` is not in `.env.example`: no container receives it yet (the API runs on the host; `check_env_example` covers compose variables only). Add it when the API joins compose.
- Template pick / run start on create (FR-R1 second half) belongs to RUN goals.

## Found for other goals
- **PII-5 (purge):** also clear `pii_masked.<field>`, the field's blind index and `pii_versions.<field>` when purging a hire's value.
- **SEC/AUD:** make `pii_access_log` append-only in `onb_data` (and move PII-4's sink to that repository).
- **Deployment:** the API's Mongo role needs insert on `org_keys` (keys made on an org's first sensitive write), insert/find on `pii_access_log`, and update on `new_hires`.
- **SEED-1:** may create org keys explicitly (`Keyring.create`); the API tolerates either.
- **UI-8 / hires UI:** use `createHire`, `listHires`, `getHire`, `updateHirePii`, `revealHirePii`, `listPiiAccessLog` from the TS client; hide a revealed value after `expires_in_seconds`.
- **PII-2:** a `put` that does not supersede until the caller commits (or a restore) would close the outage gap above.

## Decisions forced (ADRs written)
- `docs/adr/0008-hire-pii-masks-keys-and-dedupe.md`: masks stored at write time; org keys on first sensitive write; partial `{org_id, email_hash}` index; Aadhaar consent / alternative-ID type only; free text redacted before storage.
- Open decision left with Haneef (not touched): ADR 0004 cross-org unique *user* emails. Hire emails are per-org (this goal) and do not depend on it.

## Traps hit
- seed_v1 hires carry no `email_hash`, so a plain unique `{org_id, email_hash}` index fails to build on seeded data — hence `partial=`.
- `Vault.put` supersedes the previous version immediately, so a rolled-back update would strand the old value; the update path reserves the email and audits before the vault, and commits only a newer version.
- Several cross-tenant suites count `new_hires` against the seed; seeding the vault hires for every test broke them, so they are seeded on first use.
- The generic steering test reused one request three times; a creating route needs a fresh unique email per call.
