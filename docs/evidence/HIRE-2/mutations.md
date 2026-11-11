# HIRE-2 mutation checks · 2026-11-11 · compose (ob-hire-2)

Runner: `uv run --frozen python docs/evidence/HIRE-2/mutate.py` (each mutant: replace one guard, run the
named tests with `-x`, record the first failing test, restore the file). 28 mutants, 28 killed.
U5 survived the first run (the oversized tests overshot by 8 bytes and carried a Content-Length);
`test_one_byte_past_the_limit_is_refused_when_counting` was added and kills it.

| Id | Guard broken | Result |
|----|--------------|--------|
| B1 | store plaintext instead of ciphertext | KILLED by `test_document_bucket.py::test_the_gridfs_bytes_differ_from_the_plaintext` |
| B2 | hire id dropped from the AEAD binding | KILLED by `test_rebinding_the_file_to_another_hire_in_the_database_fails_to_decrypt` |
| B3 | type dropped from the binding | KILLED by `test_changing_the_bound_metadata_fails_to_decrypt[type]` |
| B4 | media type dropped from the binding | KILLED by `test_changing_the_bound_metadata_fails_to_decrypt[media_type]` |
| B5 | document id dropped from the binding | KILLED by `test_ciphertext_copied_into_another_document_of_the_same_hire_fails_to_decrypt` |
| B6 | read finds by id only, not by hire | KILLED by `test_another_hire_cannot_decrypt_them` |
| B7 | Aadhaar scans under the data key | KILLED by `test_an_aadhaar_scan_uses_the_aadhaar_key_and_a_pan_scan_the_data_key` |
| B8 | chunk numbering check removed | KILLED by `test_missing_or_reordered_chunks_are_refused[drop_last]` |
| B9 | length check removed | KILLED by `test_a_truncated_chunk_fails_closed_on_the_length` |
| B10 | no cleanup after a failed bucket write | KILLED by `test_a_failed_chunk_write_leaves_nothing_behind` |
| B11 | `alg` check removed | KILLED by `test_a_malformed_encryption_header_fails_closed[alg]` |
| U1 | media-type allowlist removed | KILLED by `test_documents.py::test_a_disallowed_media_type_is_refused[text/plain]` |
| U2 | file-signature check removed | KILLED by `test_content_that_is_not_the_declared_type_is_refused[jpeg-as-png]` |
| U3 | counted size limit removed | KILLED by `test_an_oversized_stream_without_a_content_length_is_refused` |
| U4 | Content-Length early refusal removed | KILLED by `test_an_overstated_content_length_is_refused_before_reading[5242881]` |
| U5 | counted limit off by one | KILLED by `test_one_byte_past_the_limit_is_refused_when_counting` |
| U6 | empty-body check removed | KILLED by `test_an_empty_scan_is_refused` |
| U7 | service limit doubled | KILLED by `test_an_oversized_scan_is_refused_with_413` |
| M1 | extra field written to `documents_meta` | KILLED by `test_document_bucket.py::test_documents_meta_holds_only_ids_type_uploaded_at_and_verified` |
| M2 | `verified` not written | KILLED by `test_documents.py::test_documents_meta_holds_only_ids_type_uploaded_at_and_verified` |
| S1 | hire existence (org scope) check removed | KILLED by `test_another_orgs_hire_answers_like_a_missing_one` |
| S2 | Aadhaar consent check removed | KILLED by `test_an_aadhaar_scan_needs_the_hires_aadhaar_consent` |
| S3 | audit record removed | KILLED by `test_an_upload_is_audited_without_content` |
| S4 | undo after a failed audit removed | KILLED by `test_an_upload_whose_audit_fails_leaves_nothing` |
| S5 | MANAGER allowed to upload | KILLED by `test_a_manager_may_not_upload` |
| X1 | ROUTE_CASES row renamed away | KILLED by `tools/tests_xt/test_xt_coverage.py::test_every_api_route_has_a_cross_tenant_case` |
| X2 | foreign hire answers 403 instead of 404 | KILLED by `tools/tests_xt/test_xt_api.py::test_the_routes_own_cross_tenant_checks[POST /hires/{hire_id}/documents]` |
| X3 | plaintext stored (real Mongo) | KILLED by `apps/api/tests_int/test_mongo_documents.py::test_gridfs_holds_ciphertext_that_opens_only_for_the_hire` |
