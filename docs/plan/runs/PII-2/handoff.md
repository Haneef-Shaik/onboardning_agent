# Handoff — PII-2 · Store sensitive values only in a signed, encrypted vault        attempt 1 · goal/pii-2-pii-vault · see `git log -1` (evidence captured at b8b15f2)

**Outcome claimed.** `onb_crypto.Vault` encrypts each sensitive value with AES-256-GCM. The key is the
org's data key, or its Aadhaar key for Aadhaar. The associated data binds
`org_id | subject.id | field | token | version`. Every record field except `sig` is signed with
HMAC-SHA256 under the org's signing key, and `put` returns a fresh `ptk_` token. Every read checks
the record's shape, partition and signature, then the subject and field the caller expects, before
it decrypts. Any failure raises; no value is released.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Each tamper fails closed with its own named test: changed `subject.id`, ciphertext swapped between records, changed `field`, changed `status`, edited `sig` | yes | `uv run --frozen pytest libs/py-crypto/tests -v` → `265 passed`, exit=0 (`docs/evidence/PII-2/py-crypto-tests-and-coverage.txt`).<br>Each test edits the stored document directly and is pinned to the reason `signature does not verify`, so it fails if the `sig` check alone is removed:<br>• `test_vault.py::test_tamper_changed_subject_id_fails_closed`<br>• `test_tamper_ciphertext_swapped_between_records_fails_closed` (ciphertext, nonce and key_version taken from another hire's record)<br>• `test_tamper_changed_field_fails_closed`<br>• `test_tamper_changed_status_fails_closed` (superseded→active, active→superseded, and active→purged, which the shape check refuses)<br>• `test_tamper_edited_sig_fails_closed` (a flipped bit, empty, or missing)<br>Also:<br>• `test_tamper_ciphertext_swapped_and_resigned_still_fails_on_the_aad`: with `sig` re-signed, the AAD still refuses.<br>• `test_tamper_with_any_other_signed_part_fails_closed[*]` (token, version, key_version, created_by, created_at, nonce, ciphertext)<br>• `test_a_record_signed_by_another_org_fails_closed`<br>• `test_a_malformed_record_fails_closed[*]` (14 shapes)<br>• `test_vault_review_fixes.py::test_a_replayed_active_copy_of_a_superseded_record_does_not_resolve`<br>Live on compose Mongo: `tests_int/test_vault_mongo.py::test_put_update_resolve_and_tamper_against_mongo` re-points a token with the root client and gets `VaultIntegrityError` (`docs/evidence/PII-2/vault-mongo-int.txt`, `4 passed`). |
| An update creates a new token, marks the old record `superseded`, and both still verify (test) | yes | `test_vault.py::test_an_update_creates_a_new_token_supersedes_the_old_record_and_both_verify` PASSED. It checks a new token; v1 `superseded` and v2 `active` in the stored documents; both records kept; `verify` passes for both; v2 resolves; v1 resolves only with `allow_superseded=True`.<br>Also:<br>• `test_versions_are_per_subject_and_field`<br>• `test_a_superseded_token_does_not_resolve_by_default`<br>• `test_vault_review_fixes.py::test_a_failed_supersede_leaves_no_live_old_value_and_the_next_update_cleans_up`<br>Live: `test_put_update_resolve_and_tamper_against_mongo` (`{old: (1, "superseded"), new: (2, "active")}`) and `test_a_concurrent_update_of_one_field_has_one_winner` (exactly one `active` record after 4 concurrent puts). |
| Aadhaar records land only in `pii_vault_aadhaar` and use a different key from other fields (test) | yes | **Partition:** `test_vault_aadhaar.py::test_aadhaar_records_land_only_in_the_aadhaar_partition`: the token is only in `pii_vault_aadhaar`, and the value appears in no other collection. `test_every_other_field_lands_only_in_the_general_partition`.<br>**Key:** `test_aadhaar_uses_the_aadhaar_key_and_not_the_data_key`: the Aadhaar ciphertext opens only with `KeyPurpose.AADHAAR`, a mobile ciphertext opens only with `KeyPurpose.DATA`, and the two key materials differ.<br>**Records moved between partitions:** refused (`test_an_aadhaar_record_moved_into_the_general_partition_is_not_resolved_there`, `test_a_general_record_moved_into_the_aadhaar_partition_is_refused`).<br>**Consent:** `test_aadhaar_needs_a_consent_record`, `test_the_consent_record_is_written_with_the_aadhaar_record`.<br>**Live:** `test_aadhaar_lands_only_in_its_partition_with_consent`. Root-client reads show `pii_vault` empty and the value in no other collection. |
| Tokens have the documented prefix and length and use `secrets` for randomness (test) | yes | `test_tokens.py::test_tokens_have_the_documented_prefix_and_length`: `ptk_` + 26 + 26 Crockford base32 characters = 56.<br>`test_tokens_use_secrets_for_every_random_bit`: with `secrets.token_bytes` patched, it records the calls `[10, 16]` (80 ULID bits and 128 extra bits) and checks that every random bit comes from them.<br>`test_the_token_module_imports_no_other_randomness_source`: AST check, no `random`, `uuid` or `os`.<br>Also `test_is_token_refuses_anything_not_issued_here[*]` and `test_the_ulid_part_carries_the_creation_time_and_nothing_about_a_value`. |
| `libs/py-crypto` coverage stays ≥ 80% | yes | `py-crypto-tests-and-coverage.txt`: `TOTAL 898 18 98%`, `Required test coverage of 80% reached. Total coverage: 98.00%`. `make-check.txt` shows the same in the nx `py-crypto:test` target with `--cov-fail-under=80`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Setup: `vault = Vault(keyring, store)`, where `store` is T-1's `DataStore` and `keyring` comes from PII-1. Run `await ensure_vault_indexes(store)` once at startup. It makes `{org_id, token}` and `{org_id, subject.id, field, version}` unique in `pii_vault` and `pii_vault_aadhaar`, and indexes `pii_consent`. **Concurrent updates are safe only with these indexes in place.** | `test_vault_review_fixes.py::test_the_vault_indexes_make_tokens_and_versions_unique_per_org_in_both_partitions`, `tests_int::test_the_unique_indexes_refuse_a_second_record_of_the_same_version` |
| Every call runs inside `tenant_scope(org_id)` for the same org. Another org raises `TenantViolationError`, and another org's token is `TokenNotFoundError`. | `test_the_org_must_be_the_tenant_context`, `test_another_orgs_token_is_not_found` |
| Write: `await vault.put(org_id, subject_id=, field=VaultField.X, value=, created_by=<user id>, consent=AadhaarConsent(purpose=) only for Aadhaar)` returns `ptk_…`. Store that token on `new_hires.pii.<field>`. An update returns a new token and supersedes the old one. Losing a concurrent race raises `VaultConflictError`; retry. | `test_vault.py`, `test_a_concurrent_insert_of_the_same_version_is_refused` |
| Read: `await vault.resolve(token, org_id=, subject_id=, field=)` returns the value, or `"[purged]"` (`PURGED`). It raises `TokenNotFoundError`, `TokenBindingError` (wrong subject or field), `SupersededTokenError` (pass `allow_superseded=True` only for HR audit), or `VaultIntegrityError` (tampered or malformed). `verify(...)` returns the verified `VaultRecord` without decrypting. **Catch `CryptoError`, not just `VaultError`:** keyring failures (`NoOrgKeysError`, `KeyUnwrapError`) pass through unchanged. | `test_vault.py` |
| An older record still marked `active` (a half-finished update, or a replayed copy) does not resolve without `allow_superseded`. `resolve` costs one extra indexed read to find the newest version. | `test_a_replayed_active_copy_of_a_superseded_record_does_not_resolve`, `test_a_failed_supersede_leaves_no_live_old_value_and_the_next_update_cleans_up` |
| `await vault.purge(token, org_id=, subject_id=, field=)` deletes the ciphertext and nonce, keeps a signed `purged` record, and is idempotent. The consent record is not touched. | `test_a_purged_record_resolves_to_the_purged_marker_and_keeps_no_ciphertext`, `test_purging_twice_is_a_no_op_and_a_superseded_record_can_be_purged` |
| Only `apps/api` and `libs/py-crypto` may import `Vault`, `VaultStore`, `VaultRepository` or the `onb_crypto.vault*` modules; a static test fails otherwise. Tokens, masks and `PURGED` are free to import. | `test_vault_boundary.py::test_no_module_outside_the_api_imports_the_vault` |
| `is_token(x)` is the shape check for `ptk_` tokens (a `TypeGuard[str]`). | `test_tokens.py` |
| Logs carry org, token, field, version and reason, never values. | `test_vault.py::test_the_vault_never_logs_values` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-PII-INT @ MS-6 | compose, 2026-11-10 | contributes evidence | tamper tests above, `vault-mongo-int.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/PII-2/make-check.txt`). py-crypto: `TOTAL 898 18 98%`. The intermittent py-guardrails redact property test did not fail in this run. |
| Integration | `make test-int` | exit=0 (`docs/evidence/PII-2/make-test-int.txt`): stack `12 passed`, py-data `24 passed`, py-crypto `6 passed` (4 in `test_vault_mongo.py`), py-telemetry `3 passed`. Compose project `ob-pii-2`, Caddy on 127.0.0.1:19000/19001. |

test-xt, contracts, evals and the PII scan do not apply or do not exist yet. This diff adds no route, MCP tool or event consumer.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| PII-1 | `keyring.encrypt/decrypt(..., purpose=DATA or AADHAAR)` and `associated_data(...)` length-prefixes its parts | yes. Box 3 and the AAD tests rely on it. |
| PII-1 | `key_version(org, v).key(KeyPurpose.SIGNING)` is the HMAC key, distinct from the data key | yes (`test_the_signature_covers_every_field_but_itself_with_the_signing_key`) |
| PII-1 | Keyring calls require the matching tenant context | yes. The vault adds its own `_check_org` first. |
| PII-3 | `normalize.aadhaar` canonicalises 12 digits and never echoes the value | yes (`test_aadhaar_is_stored_as_its_twelve_digits`, `test_an_aadhaar_value_of_the_wrong_shape_is_refused_without_echoing_it`) |
| T-1 | `TenantRepository` scopes every filter and insert, and `ensure_index` needs an `org_id` prefix | yes. Live tests run through `connect()`. |

## Mutation checks
The full output is in `docs/evidence/PII-2/mutation-checks.txt`, produced by `docs/evidence/PII-2/mutate.py`. The run ends with `source diff after all mutations: 0 files differ`, `survivors: none`, exit=0. All 22 mutants were killed:
- **V1** subject.id dropped from the sig payload: `test_tamper_changed_subject_id_fails_closed` failed.
- **V2** ciphertext, nonce and key_version dropped: `test_tamper_ciphertext_swapped_between_records_fails_closed` failed.
- **V3** field dropped: `test_tamper_changed_field_fails_closed` failed.
- **V4** status dropped: `test_tamper_changed_status_fails_closed` failed.
- **V5** sig never compared: every tamper test failed.
- **V6** AAD binds only org and field: `test_tamper_ciphertext_swapped_and_resigned_still_fails_on_the_aad` and the AAD test failed.
- **V7** older active records never superseded: `test_an_update_creates_a_new_token…` failed.
- **V8** version not incremented: box-2 tests failed.
- **V9** Aadhaar in the general partition: `test_aadhaar_records_land_only_in_the_aadhaar_partition` failed.
- **V10** Aadhaar under the data key: `test_aadhaar_uses_the_aadhaar_key_and_not_the_data_key` failed.
- **V11** `random` instead of `secrets`: `test_tokens_use_secrets_for_every_random_bit` failed.
- **V12** token without the 128 extra bits: `test_tokens_have_the_documented_prefix_and_length` failed.
- **V13** partition guard removed: the moved-record tests failed.
- **V14** binding check removed: `test_resolving_with_the_wrong_subject_or_field_is_refused` failed.
- **V15** superseded resolves by default: `test_a_superseded_token_does_not_resolve_by_default` failed.
- **V16** purged record may keep ciphertext: `test_a_purged_record_that_kept_its_ciphertext_fails_closed_even_when_signed` failed.
- **V17** duplicate token accepted: `test_a_duplicated_token_fails_closed` failed.
- **V18** update builds on an unverified record: `test_an_update_over_a_tampered_current_record_fails_closed` failed.
- **V19** Aadhaar without consent: `test_aadhaar_needs_a_consent_record` failed.
- **V20** live check removed: `test_a_replayed_active_copy…` failed.
- **V21** consent not rolled back: `test_a_failed_aadhaar_write_leaves_no_consent_record` failed.
- **V22** supersede miss not re-read: `test_a_failed_supersede…` failed.

RED before GREEN, recorded in:
- `red-tokens.txt`: ImportError.
- `red-vault.txt`: 2 collection errors.
- `red-review-fixes.txt`: 6 failed.

## Review findings left open
- **HIGH (residual, by design) · `vault.py` purge.**
  - Restoring a pre-purge document from a backup brings a purged value back. The vault decrypts it with the live per-org keys.
  - No row-level check can stop an attacker who can restore rows, because tombstones can be restored or deleted the same way. Only crypto-shredding (a per-hire or per-record key, deleted at purge) closes this. PRD §7.10 specifies per-org keys and "ciphertext deleted, record kept as `purged`".
  - Left for Haneef: decide whether PII-5 or SEC-1 needs crypto-shredding and an ADR. Backups must also be treated as holding live PII until then.
- **MEDIUM · `vault.py` `_plaintext`.**
  - Aadhaar is checked for shape only (12 digits). The first-digit rule and Verhoeff checksum are not enforced in the vault.
  - PRD §7.10 says "Validated on input", and `onb_guardrails.pii.is_valid_aadhaar` does it.
  - py-crypto does not depend on py-guardrails, and adding that dependency means relocking the shared `uv.lock`, so this was left out of scope. HIRE-1 must validate before calling `put`.
- **MEDIUM · `vault_indexes.py`.**
  - Race safety relies on `ensure_vault_indexes` having run. `Vault` does not check for the indexes itself.
  - HIRE-1 must call `ensure_vault_indexes` at API startup.
- **LOW (residual) · key versions.**
  - Old key versions stay usable for signing and decryption forever. A leaked v1 key could forge v1 records after rotation.
  - Needs a "minimum accepted key_version" or retirement policy, which belongs to PII-1 rotation or SEC-1.
- **LOW · `vault.py`.**
  - Keyring errors (`NoOrgKeysError`, `KeyUnwrapError`) are not wrapped as `VaultError`. This is documented above: callers catch `CryptoError`.

## Left undone, and why
- **Aadhaar Verhoeff validation:** left to the API input layer (see above).
- **Document scans (GridFS):** belong to HIRE-2.

## Found for other goals
- **HIRE-1:**
  - Call `ensure_vault_indexes(store)` at API startup.
  - Validate Aadhaar with `onb_guardrails.pii.is_valid_aadhaar` before `put`.
  - Catch `CryptoError`.
  - Keep `allow_superseded=True` for HR_ADMIN audit only.
- **PII-4:** a sink calls `resolve(...)` without `allow_superseded`. It must treat any `CryptoError` as "send nothing" and emit the guardrail and audit event.
- **PII-5:** build the scheduled job on `Vault.purge` (idempotent; leaves `pii_consent` untouched). Decide on crypto-shredding for backups (see open finding 1).
- **SEC-1:** add backup-restore and replay cases to the tamper suite.

## Decisions forced (ADRs written)
None written.
- **Superseded tokens:** `resolve` refuses them by default (`SupersededTokenError`). FR-P4 keeps them for audit, but a sink must never send an old value.
- **Consent storage:** consent is a separate tenant collection, `pii_consent`, so the vault record keeps exactly the PRD's field set.

## Traps hit
- **ruff S105:** it flags any constant named `*TOKEN*` that holds a string. The prefix constant is `PTK_PREFIX`.
- **RTK hook:** the hook truncates captured `make` output. Evidence was captured with `rtk proxy make …`.
- **Masked guards:** once decrypt failures became `VaultIntegrityError`, the AAD layer masked a missing `sig` field (V1–V3 survived). The tamper tests are now pinned to the `signature does not verify` reason.
