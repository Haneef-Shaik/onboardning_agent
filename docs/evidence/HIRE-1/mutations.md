# HIRE-1 mutation checks — 2026-11-11, branch goal/hire-1-hires-reveal

Method: `docs/evidence/HIRE-1/mutate.py` breaks one guard (exact text replacement in one file), runs
the named tests with `uv run --frozen --env-file .env pytest -x`, records the first failing test,
and restores the file from memory. M* = guards of the first build, X* = checked through test-xt and
test-int on the compose Mongo, N* = guards added after the code/security review. Hardware: the
developer's Mac, compose stack `ob-hire-1`.

| Id | Guard broken | Killed by |
|----|--------------|-----------|
| M1 | Aadhaar validator accepts anything (`fields.py`) | `test_hires.py::test_an_invalid_value_is_refused_before_anything_is_stored[aadhaar-…]` |
| M2 | validation refusals dropped (`_checked_values`) | same |
| M3 | HR may reveal Aadhaar | `test_an_hr_reveal_of_aadhaar_is_403_and_audited_as_denied` |
| M4 | MANAGER allowed on HR routes | `test_a_manager_cannot_create_a_hire` |
| M5 | reveal reason may be blank (contract pattern) | `test_a_reveal_without_a_reason_is_422[blank]` |
| M6 | reveal writes no `pii_access_log` entry | `test_every_reveal_writes_pii_access_log_readable_by_hr_admin` |
| M7 | access-log write failure swallowed | `test_no_value_is_released_when_the_access_log_write_fails` |
| M8 | same-org email pre-check removed | `test_a_duplicate_email_in_the_same_org_is_refused` |
| M9 | email index not partial | same |
| M10 | email index not unique | `test_the_unique_index_refuses_a_duplicate_that_slipped_past_the_check` |
| M11 | manager filter not applied to `GET /hires` | `test_a_manager_lists_only_their_reports` |
| M12 | manager scope not applied to one hire | `test_another_managers_report_or_another_orgs_hire_is_not_found` |
| M13 | tokens shown instead of masks | `test_hr_creates_a_hire_and_sees_every_field_masked` |
| M14 | notes stored unredacted | `test_free_text_notes_are_stored_redacted` |
| M15 | reveal reason stored unredacted | `test_a_reason_carrying_pii_is_redacted_in_the_logs` |
| M16 | raw value stored as the "mask" | `test_after_post_hires_no_raw_value_is_in_new_hires_or_the_logs` (box 1) |
| M17 | reveal without `Cache-Control: no-store` | `test_hr_admin_reveals_aadhaar_with_a_reason` |
| M18 | HR may read the access log | `test_only_hr_admin_reads_the_access_log[HR]` |
| M19 | partial filter may name `org_id` (py-data) | `test_store.py::test_a_partial_filter_is_field_exists_only[org_id]` |
| M20 | any user may be the manager | `test_the_manager_must_be_a_manager_of_the_callers_org[not-a-manager]` |
| M21 | Aadhaar without consent accepted | `test_aadhaar_needs_consent_and_excludes_an_alternative_id[aadhaar-without-consent]` |
| M22 | MANAGER may update PII | `test_a_manager_cannot_update_a_value` |
| M23 | org keys not made on first write | `test_org_keys_are_made_on_the_first_sensitive_write` |
| M24 | successful reveal not audited | `test_a_reason_carrying_pii_is_redacted_in_the_logs` |
| M25 | create race does not purge the vault | `test_the_unique_index_refuses_a_duplicate_that_slipped_past_the_check` |
| X1 | missing hire answers 403 instead of 404 | `tools/tests_xt/test_xt_api.py::test_the_routes_own_cross_tenant_checks[GET /hires/{hire_id}]` |
| X2 | email index not partial, on Mongo | `apps/api/tests_int/test_mongo_hires.py::test_the_email_index_is_unique_per_org_and_partial` |
| N1 | email not reserved before the vault on update | `test_hires_failures.py::test_losing_the_email_race_on_update_keeps_the_old_value_readable` |
| N2 | no newer-version guard on commit | `test_hires_failures.py::test_an_older_value_never_replaces_a_newer_one` |
| N3 | update audited after the vault write | `test_hires_failures.py::test_an_update_whose_audit_fails_changes_nothing` |
| N4 | `VaultConflictError` not mapped to 409 | `test_hires_failures.py::test_a_concurrent_vault_write_is_a_409_to_retry` |
| N5 | failed undo delete skips the purge | `test_hires_failures.py::test_a_create_whose_audit_and_undo_both_fail_still_purges_the_vault` |

32 mutants, 32 killed. N4 and M16 were first mis-specified in the runner (a mis-escaped pattern; a
pattern moved by the review fix), then re-run; N4 was also confirmed by hand (`assert 500 == 409`).
