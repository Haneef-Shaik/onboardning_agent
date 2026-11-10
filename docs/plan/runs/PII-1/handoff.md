# Handoff — PII-1 · Give each org its own keys, wrapped by the master key        attempt 1 · goal/pii-1-per-org-keys · see `git log -1` (evidence captured at 926899f)

**Outcome claimed.** `libs/py-crypto` (`onb_crypto`) has a per-org `Keyring`. Each org has versioned key
sets in the tenant collection `org_keys`, one document per `{org_id, key_version}`. Every set holds a
data key, a signing key, an index key, an Aadhaar key and an Aadhaar index key. Each key is wrapped
with AES-256-GCM under `PII_MASTER_KEY` and bound to `org | version | purpose`. Keys are unwrapped
only in process memory. Rotation adds a version, old versions stay readable, and a master-key re-wrap
is supported. `docs/runbooks/key-rotation.md` documents both procedures, and tests run each of their
steps.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Org A's key cannot decrypt org B's data; a wrong master key fails closed (tests) | yes | `uv run --frozen pytest libs/py-crypto/tests -v` → `158 passed`, exit=0 (`docs/evidence/PII-1/py-crypto-tests-and-coverage.txt`).<br>**Org isolation:** `test_keyring.py::test_org_a_cannot_decrypt_org_b_data` and `test_org_a_key_material_cannot_open_org_b_ciphertext_even_with_org_b_binding` (org A's raw key fails even with org B's associated data, so the check rests on the key and not only on the AAD). Also `test_org_b_aadhaar_data_is_not_readable_by_org_a_or_with_the_data_key`, `test_each_org_gets_its_own_distinct_keys_for_every_purpose`, `test_keyring_review_fixes.py::test_cached_keys_of_another_org_are_refused_in_this_tenant_context`, and `test_envelope.py::test_a_wrapped_key_moved_to_another_org_version_or_purpose_fails[*]`.<br>**Wrong master:** `test_envelope.py::test_a_wrong_master_key_fails_closed`, `test_keyring.py::test_a_wrong_master_key_fails_closed_and_returns_nothing` (active, encrypt and decrypt all raise `KeyUnwrapError`), and `test_a_wrong_master_key_fails_even_when_the_record_claims_its_id` (the GCM tag is the guard, not the id).<br>**Live:** `tests_int/test_org_keys_mongo.py::test_two_orgs_keys_are_isolated_and_a_wrong_master_fails_closed` on compose Mongo, in `make-test-int.txt` (py-crypto `2 passed`). |
| After rotation, old ciphertext still decrypts and new writes use the new version (test) | yes | `test_keyring.py::test_after_rotation_old_ciphertext_decrypts_and_new_writes_use_the_new_version` PASSED. Old `key_version 1` ciphertext decrypts, the new one is `key_version 2`, and a fresh keyring on the same store agrees.<br>Also `test_keyring_review_fixes.py::test_a_keyring_with_a_stale_cache_reads_data_written_under_a_newer_version` (another process reads v2 at once) and `test_rotation_replaces_encryption_and_signing_keys_and_keeps_index_keys`. Live: `test_the_runbook_procedures_against_mongo`. |
| No key material appears in logs (caplog test) | yes | `test_no_key_material_in_logs.py::test_no_key_material_appears_in_logs` PASSED. caplog runs at DEBUG through create, encrypt, decrypt, a failed cross-org decrypt, blind_index, rotate_orgs, a wrong-master unwrap, rewrap_orgs and bad `PII_MASTER_KEY` values. It checks every record's message, args, exc_text and `__dict__`, plus the error strings. No key (15 org keys and 2 master keys) appears in hex, upper hex, base64, urlsafe base64, repr-escaped or latin-1 form, and neither does any encoded master value. The test also asserts that ≥ 6 records and the `rotated`, `unwrap failed` and `decrypt failed` lines exist, so it cannot pass vacuously. `test_keys.py::test_keys_never_show_their_material` covers repr and str, and `test_keys_cannot_be_pickled` covers pickling. |
| The rotation runbook exists and its steps are exercised by the rotation test | yes | `docs/runbooks/key-rotation.md`: procedure A (A1–A5), procedure B (B1–B6) and key loss.<br>`test_rotation_runbook.py::test_org_key_rotation_runbook_procedure_a` and `test_master_key_rotation_runbook_procedure_b` run each step under a `# <step>.` marker.<br>`test_the_runbook_documents_exactly_the_exercised_steps` requires the runbook's `### X. ` headings to equal the test's markers, in order (`A1…A5, B1…B6`). It also checks that the runbook names the helpers it calls.<br>The same procedures run live in `tests_int/test_org_keys_mongo.py::test_the_runbook_procedures_against_mongo`. |
| `libs/py-crypto` coverage ≥ 80% | yes | `py-crypto-tests-and-coverage.txt`: `TOTAL 522 4 99%`, `Required test coverage of 80% reached. Total coverage: 99.23%`. The same appears in `make-check.txt` (nx `py-crypto:test` with `--cov-fail-under=80`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `MasterKey.from_env()` reads `PII_MASTER_KEY` (base64 of exactly 32 bytes, as from `openssl rand -base64 32`). A missing, short, long or malformed value raises `MasterKeyError` without echoing it. Services should call it at startup. | `test_keys.py::test_a_missing_master_key_fails_fast[*]`, `test_a_short_master_key_fails_fast[*]`, `test_a_master_key_that_is_not_base64_fails_without_echoing_it[*]` |
| Setup: `keyring = Keyring(MasterKey.from_env(), RepositoryOrgKeyStore(store.tenant("org_keys")))`. Run `await ensure_org_keys_index(store)` once at startup to create the unique `{org_id, key_version}` index. | `tests_int/test_org_keys_mongo.py` |
| **Every keyring call must run inside `tenant_scope(org_id)` for the same org.** That covers `create`, `rotate`, `active`, `key_version`, `verify`, `encrypt`, `decrypt`, `blind_index` and `rewrap`. A call with no context raises `NoTenantContextError`; a call for another org raises `TenantViolationError`, cached keys included. | `test_keyring_review_fixes.py::test_cached_keys_of_another_org_are_refused_in_this_tenant_context`, `test_every_call_needs_a_tenant_context[*]` |
| Org provisioning calls `await keyring.create(org_id)` once. A second call raises `KeyVersionConflictError`. Calls on an org without keys raise `NoOrgKeysError`. | `test_creating_keys_twice_is_refused`, `test_an_org_without_keys_is_refused` |
| `await keyring.encrypt(org, plaintext, associated_data=..., purpose=KeyPurpose.DATA or KeyPurpose.AADHAAR)` returns `Sealed(key_version, nonce, ciphertext)`. Store all three. `decrypt` takes the same arguments and raises `DecryptionError` on any mismatch. The AEAD binds `org, purpose, version` before the caller's `associated_data`. Other purposes are refused. | `test_keyring.py` |
| `associated_data(*parts)` length-prefixes each part, so PII-2 should bind `associated_data(org_id, subject_id, field, token, version)`. | `test_associated_data_is_length_prefixed_so_parts_cannot_shift` |
| Signing: `(await keyring.key_version(org, v)).key(KeyPurpose.SIGNING).reveal()` is the HMAC key for vault `sig`. Store the key version with the record. | `test_each_org_gets_its_own_distinct_keys_for_every_purpose` |
| `await keyring.blind_index(org, BlindIndexField.X, value)` uses the Aadhaar index key for Aadhaar and the index key for every other field. Index keys survive rotation, so stored hashes keep matching. | `test_keyring_blind_index_uses_the_aadhaar_index_key_only_for_aadhaar`, `test_blind_indexes_survive_rotation` |
| Rotation reaches running processes in two ways. A ciphertext with an unknown version triggers one reload, and the cache expires after `Keyring.DEFAULT_CACHE_SECONDS` (300). | `test_a_keyring_with_a_stale_cache_reads_data_written_under_a_newer_version`, runbook A3 |
| `SecretKey`, `MasterKey`, `OrgKeys` and `OrgKeyRecord` never show key material in repr or str and cannot be pickled. The key maps are read-only. | `test_keys.py`, `test_unwrapped_and_stored_key_maps_are_read_only` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show PII-1` lists no ledger rows for PII-1 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/PII-1/make-check.txt`). py-crypto: `158 passed`, `TOTAL 522 4 99%`. |
| Integration | `make test-int` | exit=0 (`docs/evidence/PII-1/make-test-int.txt`). Stack checks `12 passed`, py-crypto `2 passed`, py-data `24 passed`, `Successfully ran target test-int for 2 projects`. This ran on compose project `ob-pii-1` (Caddy on 127.0.0.1:18400/18401). |

The other gates (test-xt, contracts, evals, PII scan, e2e) do not exist yet. This diff adds no route, tool or consumer.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `store.tenant(name)` scopes every filter and insert. A filter naming another org raises `TenantViolationError`. | yes. Proved live: `RepositoryOrgKeyStore(...).load(ORG_B)` under `tenant_scope(ORG_A)` raises `TenantViolationError`. |
| T-1 | `ensure_index` requires an `org_id` prefix, and unique indexes are per org | yes. The `{org_id, key_version}` unique index is created and checked in `index_information()`. |
| T-1 | `TenantContext` validates org ids (`[A-Za-z0-9_-]{1,128}`) | yes. The keyring reuses it, so the `\x1f` separators in the AAD cannot appear in an org id. |
| F-2 | `make test-int` and the `mongo_tunnel`/`root_client` fixtures | yes. `libs/py-crypto/tests_int/conftest.py` loads py-data's conftest with importlib instead of copying it. |
| PII-3 | `blind_index(key, field, value)` needs a ≥ 32-byte key; Aadhaar should use its own key | yes. `Keyring.blind_index` supplies the right per-org key. |

## Mutation checks
Full output is in `docs/evidence/PII-1/mutation-checks.txt`, produced by `docs/evidence/PII-1/mutate.py`. That run ends with `source diff after all mutations: 0 files differ`, `survivors: none`, exit=0.
- **M1** (box 1): the key cache served one org's keys to every org. 7 tests failed, including `test_org_a_cannot_decrypt_org_b_data` and `test_each_org_gets_its_own_distinct_keys_for_every_purpose`.
- **M2** (box 1): unwrap swallowed a bad GCM tag and returned a random key. `test_envelope.py::test_a_wrong_master_key_fails_closed` and 7 more failed.
- **M3** (box 1): the master key id check was removed. `test_a_wrong_master_key_fails_closed_and_returns_nothing` failed; the GCM tag still refuses.
- **M4** (box 1): the wrapped key was not bound to its org. `test_a_wrapped_key_moved_to_another_org_version_or_purpose_fails[org_beta-1-data]` and `test_a_key_record_swapped_from_another_org_fails_closed` failed.
- **M5**: the store returned another org's document unchecked. `test_a_malformed_document_fails_closed[corrupt9]` failed.
- **M6** (box 2): new writes used the oldest version. `test_after_rotation_old_ciphertext_decrypts_and_new_writes_use_the_new_version` and 5 more failed.
- **M7** (box 2): only the newest version was kept. The same test, procedure A and 2 more failed.
- **M8** (box 2): rotation carried every key forward. `test_rotation_replaces_encryption_and_signing_keys_and_keeps_index_keys` failed.
- **M9** (box 3): key hex was logged at DEBUG on unwrap. `test_no_key_material_appears_in_logs` failed.
- **M10** (box 3): `SecretKey.__repr__` showed the key hex. `test_keys_never_show_their_material` failed.
- **M11** (fail-fast): a short master key was padded to 32 bytes. `test_a_short_master_key_fails_fast[0,1,16,31]` and 2 more failed.
- **M12** (review H1): the tenant-context check was removed. `test_cached_keys_of_another_org_are_refused_in_this_tenant_context` and 4 more failed.
- **M13** (review H2): no reload on an unknown version. `test_a_keyring_with_a_stale_cache_reads_data_written_under_a_newer_version` and procedure A failed.
- **M14**: a load racing a rotation cached its stale snapshot. `test_a_load_racing_a_rotation_does_not_cache_the_stale_snapshot` failed.
- **M15**: duplicate key versions were accepted. `test_duplicate_versions_in_the_store_fail_closed` failed.
- **M16**: rewrap skipped the check that nothing is left on the old master. `test_rewrap_fails_if_a_version_is_still_on_the_old_master_afterwards` failed.
- **M17** (runbook A3): the cache never expired. `test_org_key_rotation_runbook_procedure_a` failed.

RED before GREEN:
- `docs/evidence/PII-1/red-unit-tests.txt`: the tests came first, and collection failed with an ImportError.
- `docs/evidence/PII-1/red-review-fixes.txt`: the review-fix tests failed before the fix (2 collection errors, 2 failed).

## Review findings left open
I ran a code review and a security review: two general-purpose agents, each with a reviewer brief, on `git diff main...HEAD`.

Fixed:
- **H1** (code): cached keys skipped the tenant check.
- **H2** (code and security): a stale cache could not read newer versions.
- **M3** (code): a load/rotate race on the cache.
- **M4** (code) and **M3** (security): duplicate versions were not rejected, and no caller created the unique index (`ops.py` now creates it; the runbook documents it).
- The runbook overstated what re-wrap and rotation protect against after a leak. It now has "What rotation does not fix", B6, and the instruction to destroy the dump.
- Rewrap now proves the new wrapping before writing it and re-checks afterwards. It raises `KeyRewrapConflictError` (a `CryptoError`).
- `OrgKeys` and `OrgKeyRecord` maps are read-only.
- `Sealed` is validated (int version that is not a bool, 12-byte nonce, bytes).
- `pymongo` is declared as a dependency.
- The runbook step check is exact.
- The `associated_data` and `blind_index` helpers were added, and `version()` was renamed to `key_version()`.

Still open:
- **medium (security) · `keys.py` `MasterKey.from_env` · `PII_MASTER_KEY` stays in `os.environ`**, so it reaches child processes and `/proc/<pid>/environ`. Left because removing it is process-wide: other components in the same process (the API's startup and workers) may read it after the keyring does. The service entry point that owns startup (AUTH-1/API wiring) should load the key once and drop it, or read it from a secrets file. See "Found for other goals".
- **low (security) · `keys.py`/`envelope.py` · The master bytes key both the AES-GCM wrap and the HMAC fingerprint.** It is not exploitable (a 64-bit HMAC tag of a constant label), but it is not clean key separation. HKDF-derived subkeys would change the wrap format, so this is better done before any production data exists. I recommend it for PII-2 or SEC-1.
- **low (security) · `keyring.py` encrypt · Plaintext sits in a frame local.** An error reporter that captures locals would record it. That belongs to telemetry configuration (OBS goals turn off local capture), not to this library.
- **low (security) · runbook B3 · Only orgs listed in `orgs` are re-wrapped.** `org_keys` is a tenant collection and `TenantRepository` cannot list across orgs. The runbook documents the invariant (keys are created when the org is provisioned) and the check to run if provisioning ever lets the two drift.
- **low (code) · tests · Several tests monkeypatch store methods** (with an explained `type: ignore[method-assign]`). A small subclass fake would avoid it. These are cosmetic only.
- **low (code) · `tests_int/conftest.py` · Loading py-data's conftest with importlib depends on a fixed path.** A shared `onb_data.testing` module would be cleaner, but it would change py-data's packaging, so I left it for T-2/EVT-1.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal PII-1`.
- Re-encrypting old vault records under a new key version, and re-indexing blind indexes after an index-key leak, are not in the MVP. The runbook states both limits.
- `.env.example` does not list `PII_MASTER_KEY`. No compose service uses it yet, and `env-check` covers compose variables only. The goal that adds it to a container's environment should add it there.

## Found for other goals
- **PII-2:**
  - Use `associated_data(org_id, subject_id, field, token, version)` as the AEAD binding.
  - Use the `SIGNING` key of the record's `key_version` for `sig`.
  - Use `keyring.blind_index` for `email_hash`, `mobile_hash`, `pan_hash` and `aadhaar_hash`.
  - Store `Sealed.key_version` and the nonce with every record.
  - Use `KeyPurpose.AADHAAR` for `pii_vault_aadhaar`.
  - Consider HKDF separation of the master key (see open findings) before real data exists.
- **Org provisioning (AUTH-1, or whichever goal creates orgs):** call `keyring.create(org_id)` inside `tenant_scope(org_id)` when an org is created, and `ensure_org_keys_index` at startup.
- **API/worker startup:** call `MasterKey.from_env()` at startup so the service fails fast. Consider removing `PII_MASTER_KEY` from `os.environ` afterwards. Add `PII_MASTER_KEY` to `.env.example` and the compose service env.
- **OBS/telemetry:** turn off frame-local capture in any error reporter.
- **T-2:** consider a shared `onb_data.testing` module for the compose-Mongo fixtures (py-crypto now reuses them).

## Decisions forced (ADRs written)
None needed an ADR:
- **Five keys instead of three.** The card names data, signing and Aadhaar. I added `index` and `aadhaar_index`, so no HMAC key doubles as an AEAD key, and PII-3's blind index (which says Aadhaar uses its own key) gets per-org keys. PRD §7.10 requires separate keys for encryption and signing, and its own key for Aadhaar. Nothing in it contradicts the extra two.
- **Index keys carried forward on rotation,** so stored blind indexes keep matching. The runbook states the cost.
- **`cryptography` (46.x) for AES-256-GCM.** It is the standard library for this, and the PRD names AES-256-GCM. `uv.lock` changed: `cryptography`, `cffi`, `pycparser`.
- **py-crypto depends on `onb-data`,** because the keys live in Mongo and all DB access goes through `TenantRepository` (§7.9).

## Traps hit
- The session's `rtk` hook truncates `make` output. Evidence was captured with `rtk proxy make …`.
- With a cached keyring, a test that calls `forget()` before a cross-tenant assertion hides a real hole. That is how H1 was missed at first. The integration test now asserts against the cache first.
