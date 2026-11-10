# Handoff — AUD-1 · Record every privileged action in an append-only audit log        attempt 1 · goal/aud-1-audit-log · see `git log -1` (code at dd5a058)

**Outcome claimed.** One call, `await AuditLog(store).audit(actor=, action=, target=, outcome=, reason=)`, made inside `tenant_scope(org)`, appends one record:

`{_id, org_id, actor: {type: user|agent, id}, action, target, outcome, reason, trace_id, at}`

- **Where the fields come from.** `org_id` comes from the tenant context, `trace_id` from the current OTel span, and `at` from a UTC clock.
- **Append-only.** The record goes through `store.append_only("audit_log")`, whose only methods are insert, find and count. `store.tenant()`, `shared()` and `queue()` all refuse `audit_log`.
- **Reading.** HR_ADMIN reads the log at `GET /audit` with exact-match filters (`actor_type`, `actor_id`, `action`, `outcome`, `target`, `since`, `until`). Results are newest first, paged with `page`/`limit` (limit ≤ 200, at most 10 000 records deep), and `meta.page` carries the total. HR and MANAGER get 403.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| The audit repository has no update or delete path (test) | yes | `docs/evidence/AUD-1/box-tests.txt` (`uv run --frozen pytest -v libs/py-data/tests/test_audit.py apps/api/tests/test_audit_route.py apps/api/tests/test_role_matrix.py libs/py-contracts/tests/test_audit_contracts.py` → `144 passed`, exit=0). These PASSED:<br>• `test_the_append_only_repository_has_no_update_or_delete_path`: the public surface is exactly `{collection_name, count, find, insert_one}`, and the class is not a `_Repository`.<br>• `test_the_audit_log_has_no_update_or_delete_path`: `AuditLog`'s surface is exactly `{audit, ensure_indexes, list}`, no attribute is a repository, and no name contains update/delete/replace/remove/drop/upsert/modify/rename/aggregate.<br>• `test_no_attribute_reachable_from_the_audit_log_is_a_full_repository` walks every slot, private ones included.<br>• `test_the_audit_collection_is_never_handed_out_with_a_write_path[tenant\|shared\|queue]`.<br>• `test_the_audit_log_never_calls_a_driver_write_other_than_insert`.<br>• `test_a_unique_index_on_the_audit_log_is_refused`.<br>Live: `apps/api/tests_int/test_mongo_audit.py::test_the_store_hands_out_no_writable_view_of_the_audit_log`.<br>Mutations M1–M4 and M16. |
| HR and MANAGER get 403 on `GET /audit`; HR_ADMIN gets 200 (test) | yes | `box-tests.txt` PASSED:<br>• `test_hr_and_manager_get_403_on_get_audit[HR]` and `[MANAGER]` (403, `FORBIDDEN`, `data: null`).<br>• `test_hr_admin_gets_200_on_get_audit`.<br>• `test_role_matrix[GET /audit-HR_ADMIN\|HR\|MANAGER]`, from the new `ROLE_MATRIX` row `{HR_ADMIN: 200, HR: 403, MANAGER: 403}`.<br>• `test_a_refused_caller_never_reaches_the_audit_log[*]`: the log is never read on a 403.<br>• `test_every_guarded_route_refuses_a_missing_token[GET /audit]`.<br>Live against Mongo: `test_hr_and_manager_get_403_against_the_real_log[*]` and `test_hr_admin_reads_only_their_orgs_records_newest_first` (in `make-test-int.txt`, api 18 passed).<br>Mutations M7 and M8. |
| A record carries the current span's trace id (test) | yes | `box-tests.txt` PASSED:<br>• `test_a_record_carries_the_current_spans_trace_id`: the written `trace_id == f"{span trace_id:032x}"`.<br>• `test_two_spans_give_two_trace_ids`.<br>• `test_a_record_outside_any_span_has_no_trace_id` (null).<br>• `test_current_trace_id_reads_the_active_span`.<br>• `test_the_in_memory_log_takes_the_current_spans_trace_id`.<br>Live: `test_records_land_in_mongo_with_org_and_the_spans_trace_id`, read back with the root client, and the API returns the same id (`test_hr_admin_reads_only_their_orgs_records_newest_first`).<br>Mutations M5 and M6. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Write: `from onb_data import AuditLog, Actor, AuditOutcome`; `log = AuditLog(store)`. Inside `tenant_scope(org)`: `await log.audit(actor=Actor.user(uid) \| Actor.agent(name), action="pii.reveal", target="hire:h_1/pan", outcome=AuditOutcome.SUCCESS\|DENIED\|FAILED, reason=None\|str)` returns the record id (32 hex). | `test_one_audit_call_writes_one_org_scoped_record`, live tests |
| Depend on the `onb_data.Auditor` protocol. The test double is `onb_data.testing.InMemoryAuditLog`; `onb_api.testing` re-exports it. It uses the same `build_record`, so it refuses the same input. It takes the trace id from the current span, keeps records per org, and has the same page bounds. `.now`/`.trace` fix the clock and trace id. | `test_both_logs_satisfy_the_auditor_protocol`, `test_the_in_memory_log_refuses_what_the_real_one_refuses[*]`, `test_the_in_memory_log_is_per_org_and_paged_like_the_real_one` |
| Input rules:<br>• `action` is dotted lower case, ≤ 64 characters (`mcp.tool_call`).<br>• `target` is `kind:ref` ≤ 256 characters, with no spaces or `@` (`hire:h_1/pan`, `find:pii_vault`, `pii:ptk_…`).<br>• An actor id matches `[A-Za-z0-9_.:-]{1,128}`, so no emails.<br>• `reason` is 1–1000 characters or None.<br>• Breaking a rule raises `TypeError`/`ValueError` before the driver is called. | `test_bad_fields_are_refused_before_the_driver[*]`, `test_a_target_is_a_kind_and_an_id_with_no_free_text[*]`, `test_an_email_is_not_an_actor_id` |
| **Failure contract: fail closed.** `audit()` raises `NoTenantContextError` with no tenant context, and lets any DB error propagate. Callers must not report a privileged action as done if its audit write failed. Inside `store.run_transaction` the record joins the transaction and is rolled back with it, so write DENIED/FAILED records outside the transaction that failed. | `test_audit_without_a_tenant_context_raises_and_writes_nothing`, `test_a_driver_error_propagates_from_audit`, module docstring |
| `AuditLog.list(AuditQuery(...), page=, limit=)` returns an `AuditPage(entries, total, page, limit)`, newest first by `(at, _id)`. `check_page` caps the skip at `MAX_AUDIT_SKIP = 10_000`. `ensure_indexes()` creates `org_at` = `(org_id, at desc, _id desc)`; the API's lifespan calls it. | `test_list_sends_a_scoped_filter_newest_first_with_skip_and_limit`, `test_deep_pages_are_refused_before_the_driver`, `test_startup_makes_the_org_prefixed_index` (live) |
| `create_app(..., audit: AuditReader \| None)`. Without a log, `GET /audit` answers 503. `onb_api.main` wires `AuditLog(store)`. | `test_get_audit_without_an_audit_log_is_503`, `test_startup_makes_the_audit_indexes` |
| `APPEND_ONLY_COLLECTIONS == {"audit_log"}`. `store.append_only(name)` refuses any other name. Unique indexes on append-only collections are refused. | `test_audit_log_is_the_only_append_only_collection`, `test_append_only_is_refused_for_other_collections` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show AUD-1` lists no ledger rows for AUD-1 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. Lint and typecheck for 17 projects; py-data coverage 99.26% (`docs/evidence/AUD-1/make-check.txt`). |
| Integration | `make test-int` | exit=0, `Successfully ran target test-int for 6 projects`; api `18 passed`, which includes the 10 new tests in `test_mongo_audit.py` (`docs/evidence/AUD-1/make-test-int.txt`). Compose project `ob-aud-1`, Caddy on 19700/19701. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date`, exit=0 (`make-contracts-check.txt`). `listAudit` and the `AuditRecordsEnvelope` schemas were regenerated. |

`make test-xt` (T-3), the evals and the PII scan (SEC-1) do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `scoping.scoped_filter`/`scoped_document` add `org_id` and refuse another org; `tenant_scope`/`current_tenant` | Yes. `AppendOnlyRepository` uses them (M9 and M10 caught; `test_a_record_cannot_name_another_org`). |
| T-1/EVT-1 | `session_kwargs()` joins the ambient transaction | Yes. It is used, and its effect is documented in the failure contract. |
| T-1 | `connect(tz_aware=True)`, so Mongo returns aware datetimes | Yes. The live API answer is `…Z` UTC. |
| AUTH-1 | `require_role(...)` gives 403; every route needs a `ROLE_MATRIX` row | Yes. A row was added, and the completeness tests pass. |
| C-1 | Envelope `meta.page`; the route walk forbids org inputs | Yes. `test_no_request_model_or_parameter_declares_org_id` still passes with `/audit`'s parameters. |

## Mutation checks
`docs/evidence/AUD-1/mutation-checks.txt` (script: `docs/evidence/AUD-1/mutate.py`): `17/17 mutations caught`, exit=0. The script restores every file it mutates; afterwards there are no changes under `libs/` or `apps/`.
- M1, `tenant()` no longer refuses `audit_log` → `test_the_audit_collection_is_never_handed_out_with_a_write_path[tenant]`.
- M2, the repository gains a `delete_one` → `test_the_append_only_repository_has_no_update_or_delete_path`.
- M3, `AuditLog.update` added, and M4, a `repository` property → `test_the_audit_log_has_no_update_or_delete_path`.
- M5, the span is ignored → `test_a_record_carries_the_current_spans_trace_id` and `test_two_spans_give_two_trace_ids`.
- M6, the `is_valid` check is dropped → `test_a_record_outside_any_span_has_no_trace_id` and 2 more.
- M7, HR is allowed → `test_hr_and_manager_get_403_on_get_audit[HR]` and `test_role_matrix[GET /audit-HR]`.
- M8, all roles are allowed → 6 failed.
- M9, reads are not scoped, and M10, inserts are not scoped → 3 each.
- M11, `since > until` is accepted → `test_bad_query_parameters_are_422[since+until]`.
- M12, oldest first → 1 failed.
- M13 and M14, the page depth is not capped (data layer and route) → 2 and 1 failed.
- M15, free-text targets are accepted → 7 failed.
- M16, a unique index is allowed → 1 failed.
- M17, a naive clock is accepted → `test_a_naive_clock_is_refused`.

RED before GREEN:
- `red-py-data.txt` and `red-api.txt`: ImportErrors, because the tests were written before the code.
- `red-review-fixes.txt`: collection error, because the review-fix tests were written before `build_record`, `Auditor` and `onb_data.testing` existed.

## Review findings left open
I ran a code review and a security review: two general-purpose agents, each with a reviewer brief, on `git diff main...HEAD` at c39582c.

Fixed:
- **High** (both reviews): an unbounded `page` overflowed the skip and returned a 500.
- **Medium:** the fake and the real log diverged. They now share `build_record`, and the fake has moved to `onb_data.testing`.
- **Medium:** there was no writer protocol for other packages. `Auditor` was added.
- **Medium:** the failure contract is now documented and tested.
- **Medium (partly):** PII could get into `target` and the actor id. `target` grammar now excludes spaces and `@`, and actor ids exclude `@`.
- **Low:** a naive clock was accepted.
- **Low:** a unique index was allowed on `audit_log`.

Still open:
- **medium** · `audit.py` `reason` · `reason` is free text (1–1000 characters), so a caller could put raw PII in it. Left because the PII detectors live in `onb_guardrails`, and making py-data depend on them is outside this card. SEC-1 (`make test-pii`) and GR-1 should scan audit `reason` values, or callers should pass fixed reason codes.
- **medium** · DB level · The app's Mongo user can still update or delete `audit_log` directly. The guarantee holds at the code level only (append-only repository, no other handout). Least-privilege Mongo roles (insert + find only on `audit_log`) belong to MCP-3 and the deployment goal.
- **low** · `append_only.py` count · `count_documents` on a filter by `actor.id`, `action` or `target` scans the org's records; only `(org_id, at)` is indexed. This is HR_ADMIN-only and depth-capped. Add indexes when the UI-8 views show which filters are used.
- **low** · `AuditEntry.from_document` · One malformed stored record makes its page return a 500. Every write is validated, so this needs data written outside the code.
- **low** · `list` · `find` and `count` are two reads, so the total can drift under concurrent inserts, and skip/limit pages shift as records arrive. Acceptable for the MVP; keyset paging on `(at, _id)` would fix it later.
- **low** · The `since > until` check exists in both the route (422 with a detail) and `AuditQuery` (`ValueError`). This is deliberate: the route gives the field-level error.
- **info** · `_collection` can still be reached as a private attribute (Python convention; T-2's command listener is the runtime net).

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal AUD-1`.
- `GET /audit` reads are not audited themselves. The card doesn't ask for it.

## Found for other goals
- **All writers (T-3, HIRE-1, MS-1, REG-1, GR-1, TPL-2, RUN-2):**
  - Depend on `onb_data.Auditor` and test with `onb_data.testing.InMemoryAuditLog`.
  - Targets are `kind:id`; never put a name, email or PAN in `target` or `reason`.
  - Fail closed on an audit error.
  - Write DENIED/FAILED records outside a transaction that is rolling back.
- **T-3:** add `GET /audit` to the cross-tenant suite. The unit and live tests already show an acme HR_ADMIN never sees globex records, and that an `org_id`/`orgId`/`tenant`/`org` query parameter is ignored.
- **MCP-3 / deployment:** give the app's Mongo role insert and find only on `audit_log`, and no MCP database user any access to it.
- **SEC-1:** include `audit_log.reason` in the PII scan.
- **UI-8:** use `listAudit` from the TS client. Filters are exact-match; `since` and `until` need a time zone.

## Decisions forced (ADRs written)
None needed an ADR:
- **py-data now depends on `opentelemetry-api`** (uv.lock +5 lines), so the record can read the current span. This is the API package only, not the SDK. The audit writer lives in `onb_data` so the MCP hub, guardrails and orchestrator can call it without depending on `onb_api`.
- **Contract enums are copied** in `onb_contracts.audit` and `onb_data.audit`, so py-data does not depend on the API contracts. `test_contract_enums_match_the_data_layer` keeps them in step.
- No PRD conflict found. PRD §7.6 says "`audit_log` (append-only), HR_ADMIN UI" and §10 lists `GET /audit` as HR_ADMIN; both are built as written.

## Traps hit
- The worktree guard refuses long inline shell scripts and compound git commands. Edits ran as Python files in the job temp directory, and git commands ran one at a time.
- The session's output hook compresses `make` output even when it is redirected to a file. Evidence files were captured with `rtk proxy make …` to keep the full output.
- The machine restarted mid-session. Work was committed up to c39582c and resumed from the checkpoint; both reviews were re-run after a rate limit.
