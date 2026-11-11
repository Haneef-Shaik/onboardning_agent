# Handoff — HIRE-2 · Store document scans encrypted; agents see only metadata        attempt 2 · goal/hire-2-encrypted-documents · see `git log -1` (handoff commit)

**Outcome claimed.** `POST /hires/{hire_id}/documents?type=…` (HR/HR_ADMIN) takes a PDF/JPEG/PNG scan of at
most 5 MiB as the raw body, seals it with AES-256-GCM under the org keyring (Aadhaar key for Aadhaar scans)
bound to `org | hire | document id | type | media type`, stores the ciphertext in a GridFS-layout bucket
(`documents.files`/`documents.chunks`) through `TenantRepository`, and writes `documents_meta` with only
`_id, org_id, hire_id, type, uploaded_at, verified`; the answer is that metadata only.

Code: `apps/api/src/onb_api/documents/` (bucket, meta, upload, service, routes), `apps/api/src/onb_api/hires/org_keys.py`
(org-key creation shared with HIRE-1's service), contracts `libs/py-contracts/src/onb_contracts/documents.py`,
wiring `app.py` (`create_app(..., documents=)`, each addition last in its list) and `main.py`.
Decisions: `docs/adr/0012-encrypted-document-scans.md`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| GridFS bytes differ from the plaintext and decrypt only with the matching hire binding (test) | yes | Unit (in-memory driver under the real `DataStore`/keyring): `apps/api/tests/test_document_bucket.py::test_the_gridfs_bytes_differ_from_the_plaintext`, `::test_the_bytes_decrypt_with_the_matching_hire_binding`, `::test_another_hire_cannot_decrypt_them`, `::test_rebinding_the_file_to_another_hire_in_the_database_fails_to_decrypt`, `::test_changing_the_bound_metadata_fails_to_decrypt[type,media_type]`, `::test_ciphertext_copied_into_another_hires_document_fails_to_decrypt`, `::test_ciphertext_copied_into_another_document_of_the_same_hire_fails_to_decrypt`, `::test_a_flipped_byte_fails_to_decrypt`, `::test_missing_or_reordered_chunks_are_refused[…]`; through the route `test_documents.py::test_the_stored_bytes_are_encrypted_and_open_only_for_the_hire`, `::test_moving_a_scan_to_another_hire_in_the_database_does_not_open_it`. Real Mongo: `apps/api/tests_int/test_mongo_documents.py::test_gridfs_holds_ciphertext_that_opens_only_for_the_hire` (root read-back of chunks, every collection scanned for the plaintext marker, re-point → `DecryptionError`). `uv run pytest apps/api/tests -q` → `532 passed` (after the rebase on TPL-2); `make test-int` → api `39 passed` (`test_mongo_documents.py` alone: `3 passed`). Mutants B1–B11, X3 killed. |
| Oversized files and disallowed MIME types are rejected (tests) | yes | `test_documents.py::test_an_oversized_scan_is_refused_with_413`, `::test_a_scan_of_exactly_the_limit_is_accepted`, `::test_an_oversized_stream_without_a_content_length_is_refused`, `::test_an_understated_content_length_does_not_lift_the_limit`, `::test_an_overstated_content_length_is_refused_before_reading`, `::test_one_byte_past_the_limit_is_refused_when_counting`; `::test_a_disallowed_media_type_is_refused[text/plain, text/html, image/gif, image/svg+xml, application/zip, application/octet-stream, application/x-msdownload, image/heic]`, `::test_a_missing_content_type_is_refused`, `::test_content_that_is_not_the_declared_type_is_refused[jpeg-as-png, pdf-as-jpeg, png-as-pdf, exe-as-png, html-as-pdf]`, `::test_an_empty_scan_is_refused`; Mongo `test_mongo_documents.py::test_an_oversized_scan_stores_nothing_on_mongo`. Mutants U1–U7, R7 killed. |
| A schema test shows `documents_meta` holds only ids, `type`, `uploaded_at` and `verified` | yes | `test_document_bucket.py::test_documents_meta_holds_only_ids_type_uploaded_at_and_verified` (stored keys == `{_id, org_id, hire_id, type, uploaded_at, verified}` and exact values), `test_documents.py::test_documents_meta_holds_only_ids_type_uploaded_at_and_verified` (through the route), `test_mongo_documents.py::test_documents_meta_on_mongo_holds_only_ids_type_uploaded_at_and_verified` (real Mongo); contract `libs/py-contracts/tests/test_document_contracts.py::test_document_meta_carries_only_ids_type_upload_time_and_verified`, `::test_document_meta_refuses_any_other_field[…]`. Mutants M1, M2 killed. |
| `make test-xt` covers the new route | yes | `tools/tests_xt/test_xt_api.py::ROUTE_CASES[("POST", "/hires/{hire_id}/documents")]` (own check: org B's hire → 404 indistinguishable from missing for HR and HR_ADMIN; org B's scan not in org A's `documents_meta` and does not open for org A; still opens for org B) + generic leak/steering checks per role. `make test-xt` (inside `make check`) → `cross-tenant surface: 22 API routes, 8 MCP tools, 0 event consumers`, lists `route POST /hires/{hire_id}/documents`, `203 passed`; the 7 new cases shown PASSED with `-rA`. Mutants X1 (coverage test), X2 (403 instead of 404) killed. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `DocumentService.build(store, keyring, *, auditor)` — must get the **same keyring** as `HireService` (one master key). `create_app(..., documents=)`; without it the route answers 503. Startup makes the bucket (`{org_id, files_id, n}` unique on chunks, `{org_id, metadata.hire_id}` on files) and `documents_meta` (`{org_id, hire_id}`) indexes. | `test_documents.py::test_without_a_document_service_the_route_answers_503`, `test_mongo_documents.py` (index read-back) |
| Upload contract: raw body, `Content-Type` ∈ {`application/pdf`,`image/jpeg`,`image/png`} (params/case ignored), `?type=` ∈ `DocumentType` (`aadhaar, pan, passport, voter_id, driving_licence`), ≤ `MAX_DOCUMENT_BYTES` (5 MiB). 201 → `DocumentMetaEnvelope` `{id: "doc_<24 hex>", hire_id, type, uploaded_at, verified: false}`. 413 `PAYLOAD_TOO_LARGE`; 422 with detail field `header.content-type` / `body` / `query.type`; 404 for another org's or a missing hire. TS client: `uploadHireDocument` (body typed `string` per media type — pass a `Blob` with a `bodySerializer`). | box 2 tests; `libs/ts-api-client/openapi.json` |
| An Aadhaar scan needs the vault's `pii_consent` record for that hire (written when HR enters the hire's Aadhaar with consent); a refusal is audited `document.upload` DENIED, target `hire:<id>/document`. The `new_hires.id_proof` flag is **not** trusted (agents can write `new_hires`). | `test_an_aadhaar_scan_needs_the_hires_aadhaar_consent`, `test_a_consent_flag_forged_on_the_hire_does_not_open_the_gate`, `test_another_hires_consent_does_not_count`, `test_an_aadhaar_consent_refusal_is_audited_as_denied` |
| Success audit: `document.upload` SUCCESS, target `hire:<id>/document/<doc id>`, no reason. If the metadata or audit write fails, chunks, files document and metadata are removed and the request is 500. | `test_an_upload_is_audited_without_content`, `test_an_upload_whose_audit_fails_leaves_nothing`, `test_an_upload_whose_metadata_write_fails_leaves_no_bytes`, `test_an_upload_whose_audit_and_metadata_undo_both_fail_still_removes_the_bytes` |
| `DocumentService.read(org_id, hire_id=, document_id=)` / `DocumentBucket.read` return the plaintext only for the hire it was uploaded for; `DocumentNotFoundError` (a `LookupError`) for another hire/org or a malformed id; `DecryptionError` for any tampering or malformed stored header/length/chunks. No route serves bytes yet. Must run inside `tenant_scope(org_id)` (the keyring checks it). | box 1 tests; `test_a_malformed_encryption_header_fails_closed[…]`, `test_a_malformed_length_fails_closed[…]`, `test_an_unknown_key_version_fails_closed`, `test_only_a_document_id_is_looked_up[…]` |
| `ensure_org_keys(keyring, org_id)` in `onb_api.hires.org_keys` (HIRE-1's logic, moved): make an org's keys on its first sensitive write. | `test_an_org_without_keys_gets_them_on_its_first_upload`; HIRE-1's tests unchanged and passing |
| API unit fixtures: `conftest.py` gains a shared `keyring` fixture (used by `hires` and `documents`) and a `documents` fixture; `app` passes `documents=`. | `uv run pytest apps/api/tests` → 455 passed |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | HIRE-2 appears in no ledger row (`grep HIRE-2 docs/plan/ACCEPTANCE.md` → 0 matches) | — | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check (incl. test-xt) | `make check` | exit 0 — `make check: all passed`; `NX Successfully ran target lint/typecheck/test for 17 projects`; test-xt `cross-tenant surface: 22 API routes, 8 MCP tools, 0 event consumers` · `203 passed` (attempt 2, rebased on `main` ec3dd2a) |
| Integration | `make test-int` | exit 0 — `NX Successfully ran target test-int for 8 projects` (api `39 passed`, incl. `test_mongo_documents.py` 3) |
| Contracts | `make contracts` then `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (regenerated after the rebase: TPL-2's 9 template operations and `uploadHireDocument` all present; both files committed) |
| Coverage of the new package | `pytest apps/api/tests --cov=onb_api.documents` | 100% (259 stmts at the time; all branches incl. fail-closed paths) |
| Eval self-test | not run | diff touches no agent, prompt, manifest or guardrail |

Environment: compose project `ob-hire-2` (ports 19300/19301), `LLM_CONNECTOR=fake`, no model calls.

## Rebase on main (attempt 2)
`main` gained TPL-2 (templates lifecycle, `ec3dd2a`) after this branch was cut. Rebased onto `ec3dd2a`, keeping
both sides everywhere:
- `apps/api/src/onb_api/app.py` / `main.py`: `templates=` and `documents=` both passed; lifespan, `app.state`
  and routers keep TPL-2's lines with HIRE-2's appended. TPL-2's `BodyLimitMiddleware` applies to `/templates`
  only, so it does not cap document uploads (HIRE-2 keeps its own 5 MiB counted limit).
- `apps/api/tests/conftest.py`: TPL-2's `audit_log`, `registry_retire`, `template_store`, `templates` fixtures
  kept; HIRE-2's shared `keyring` (now also used by `hires`) and `documents` added; `app` takes both.
- `apps/api/tests/test_role_matrix.py`: all TPL-2 rows and sample paths kept, HIRE-2's row/path appended.
- `tools/tests_xt`: all 21 rows on `main` kept (script-checked: none missing); HIRE-2's row adapted to the new
  `check(client, xt, unscoped)` signature via `_reads_only` (its check uploads to org B on purpose, so a root
  snapshot of org B would not stay unchanged); `build_api` passes `templates=` and `documents=`.
- OpenAPI/TS client regenerated with `make contracts`.
Spot mutants re-run on the rebased code: B2, B6, S1, U3, X1 all killed.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| HIRE-1 | `HireStore.get` scoped to the token's org; `HireService.build`; org keys on first write (ADR 0007) | yes — reused (`ensure_org_keys` extracted unchanged); 404 for foreign hires (S1, X2) |
| PII-1 | `Keyring.encrypt/decrypt` with caller associated data bound under org/purpose/version; `KeyPurpose.AADHAAR` | yes — B2–B5, B7, R3 |
| PII-2 | vault writes a `pii_consent` record with each Aadhaar value | yes — the consent gate reads it (R1) |
| T-1/T-2 | `TenantRepository` scoping; safety net in Mongo suites | yes — all bucket/meta access via `store.tenant(...)`; root reads in `unscoped(...)` |
| T-3 | ROUTE_CASES per route; foreign id == missing id | yes — new row, X1, X2 |
| TPL-2 | `create_app(templates=)`, xt `check(client, xt, unscoped)` + `_reads_only`, `BodyLimitMiddleware` on `/templates` | yes — kept intact through the rebase; 22-route surface green |
| REG-1 | `documents_meta` in db_agent's read/write lists; MCP roles are allowlists | yes — `documents.files`/`.chunks` are in neither list, so agents cannot reach the bytes (checked in `apps/mcp-mongo/src/onb_mcp_mongo/db_users.py`) |
| AUD-1 | audit fail-closed, `kind:ref` targets | yes — S3, S4, R2 |

## Mutation checks
34 mutants, 34 killed: `docs/evidence/HIRE-2/mutations.md` (runner `docs/evidence/HIRE-2/mutate.py`). One
survivor in an earlier run (U5, off-by-one on the counted limit) led to a new boundary test; one equivalent
mutant (R5) led to removing a redundant guard.

## Review findings left open
Code review and security review (both: no critical/high). Fixed: consent gate moved off the agent-writable
`id_proof` flag (security MEDIUM); removal deletes chunks first, each step independent (code MEDIUM); huge
tampered length, unknown key version, non-bytes nonce, operator ids, non-ASCII Content-Length; consent refusal
audited; assertions pinned with `match=`. Left open:
- MEDIUM · `documents/service.py` upload / `bucket.py` put · the undo is in-process: a crash between the bucket write and the metadata write leaves ciphertext without `documents_meta` (still encrypted and org-scoped). No transaction because the unit-test store has no client. → sweep with PII-5 (ADR 0012 §7).
- LOW · only the Aadhaar-consent refusal is audited; 413/422 validation refusals are not (they store nothing).
- LOW · memory: ~4 copies of a ≤5 MiB scan per request and no cap on concurrent uploads → rate limiting (SEC goal).
- LOW · the signature check is a prefix check (polyglots pass); harmless while no route serves bytes → a download goal must serve with the stored media type, `nosniff`, `attachment`, sandbox CSP.
- LOW · `read()` takes `org_id` explicitly; the keyring rejects any org other than the tenant context, so a mismatch fails closed.
- LOW · `DocumentService.bucket`/`.meta` are public attributes (used by tests only).

## Left undone, and why
- No `GET` listing or download of documents: the card names only `POST /hires/{id}/documents`. UI-4 can show the metadata the upload returns; a listing route (and an audited download that sets `verified`) belong to later goals.

## Found for other goals
- **UI-4:** upload with `uploadHireDocument` (raw `Blob` body, `Content-Type` of the file, `?type=`); to list a hire's documents after reload it needs a `GET /hires/{id}/documents` (metadata only) — not built here.
- **PII-5 (purge/retention):** purge scans with their hire: delete `documents.chunks` then `documents.files` for the hire (index `{org_id, metadata.hire_id}`), keep `documents_meta`; sweep files documents with no `documents_meta` (crash orphans).
- **mcp-mongo / SEC:** protect `id_proof.*` on agent writes to `new_hires` like `pii.*` (it is display state; this goal no longer trusts it, but HIRE-1's record shows it). Consider listing `documents.files`/`documents.chunks` in `DENIED_COLLECTIONS` explicitly (already unreachable via the allowlists).
- **Deployment:** the API's Mongo role needs insert/find/remove on `documents.files`, `documents.chunks`, `documents_meta`, find on `pii_consent`. Any reverse proxy must allow ≥ 5 MiB bodies on this route (Caddy has no limit configured today).

## Decisions forced (ADRs written)
- `docs/adr/0012-encrypted-document-scans.md`: GridFS layout written through `TenantRepository` (no driver GridFS bucket — `DataStore` exposes no database); one AEAD over the whole scan then chunked, binding org/hire/doc/type/media type; Aadhaar key for Aadhaar scans; consent from the vault's `pii_consent`; `documents_meta` exact shape, no file name; raw body (multipart would spool plaintext to a temp file); 5 MiB, 413/422 codes; write order and undo.

## Traps hit
- `memory_keyring()` makes a fresh master key per call: the hire and document services must share one keyring, or keys made by one cannot be unwrapped by the other (conftest `keyring` fixture).
- PyMongo's async client is bound to the event loop that first used it: integration read-backs open their own store instead of reusing the app's service from `asyncio.run`.
- Without a byte cap on the counted path, a test that only overshoots with a Content-Length cannot tell an off-by-one (U5).
