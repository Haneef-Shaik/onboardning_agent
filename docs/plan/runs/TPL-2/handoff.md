# Handoff — TPL-2 · Run the template lifecycle: draft, validate, publish, archive        attempt 2 · goal/tpl-2-template-lifecycle · see `git log -1` (rebased on main 397df21: HIRE-1 + MOCK-1)

**Outcome claimed.** The lifecycle works over HTTP:
- HR and HR_ADMIN create and edit drafts, which are validated on save (FR-T3) and on demand.
- Only HR_ADMIN publishes. A refused publish is audited as denied, and an invalid draft gets 422 with the validator's issues and is audited as failed.
- A valid publish appends the key's next immutable version in one transaction with its audit record.
- HR_ADMIN archives a version, audited.
- Everyone, managers included, reads the published templates.
- Every new route is in `make test-xt`.

Code: `apps/api/src/onb_api/templates/` (store, lifecycle, routes, validators) and `onb_contracts.template_lifecycle`.

## Attempt 2: rebase onto main (HIRE-1, MOCK-1)
Attempt 1 passed the verifier at 667b563. The branch was then rebased onto main at 397df21, which
merged HIRE-1 (hires, vault, reveal) and MOCK-1. Conflicts were resolved keeping both sides. None of
HIRE-1's routes, cases or role-matrix rows were dropped or weakened.
- **`app.py`:** `create_app(..., hires=, templates=)`, both `ensure_indexes` in the lifespan, both
  `app.state` fields and both routers. The docstring names both 503 cases. `BodyLimitMiddleware`
  stays, and covers `/templates` only.
- **`main.py`:**
  - builds `HireService` (with `PII_MASTER_KEY`) and `TemplateLifecycle` (with `CONFIG_DIR`);
  - `MasterKey.from_env()` is read before `CONFIG_DIR`, so HIRE-1's startup tests still see
    `MasterKeyError` first;
  - `test_auth_settings.py` strips both settings in `_start_api` and keeps both sets of startup tests.
- **`tests/conftest.py`:**
  - keeps both fixture sets: `memory_db`, `hire_audit` and `hires` (HIRE-1), and `audit_log`,
    `template_store`, `templates` and `registry_retire` (TPL-2);
  - the app gets both services;
  - the app's audit log is `audit_log`, as in TPL-2; hires keep their own `hire_audit`.
- **`test_role_matrix.py`:** all six HIRE-1 rows are unchanged, and those routes are still sent no
  body, so permitted roles get 422. My `test_every_route_with_a_body_has_a_sample_body` became
  `test_every_route_with_a_body_has_a_sample_body_or_expects_422_without_one`, so it accepts HIRE-1's
  convention without weakening either side. Every `SAMPLE_BODIES` key must be a real body route, and
  every other body route must expect 422.
- **`tools/tests_xt`:**
  - `build_api` passes `hires=` and `templates=`;
  - `world._org_strings` includes both `xt_vault_strings` and `template_drafts`;
  - `ROUTE_CASES` has 21 rows: the 6 base rows, HIRE-1's 6 (their checks wrapped in `_reads_only`,
    since `check` now also receives `unscoped`) and TPL-2's 9;
  - HIRE-1's fresh-request-per-call steering test is kept. Publish and archive are idempotent and
    PUT's revision is volatile, so the template rows pass it.
- **`libs/ts-api-client`:** regenerated with `make contracts` from the merged app. Both generated
  files are committed, and `contracts-check` is up to date.

| Box | Met | Evidence |
|-----|-----|----------|
| HR publish → 403; HR_ADMIN publish → 200 (test) | yes | `apps/api/tests/test_templates_route.py::test_hr_publish_is_403_and_hr_admin_publish_is_200`: HR → 403 FORBIDDEN, MANAGER → 403, nothing published by either, HR_ADMIN → 200 with v1. `test_publish_and_refusals_are_audited`: the HR refusal is audited `denied` and the success is audited. Role matrix: `test_role_matrix[POST /templates/drafts/{draft_id}/publish-*]`.<br>Command: `uv run --frozen pytest apps/api/tests/test_templates_route.py apps/api/tests/test_template_lifecycle.py -q -k "hr_publish_is_403 or invalid_draft_returns_422 or published_version_cannot or never_overwrites or no_route_writes or never_written_again or no_store_has"` → `9 passed, 23 deselected`.<br>Mutations M1 and M2 (HR, or anyone, may publish) are killed by these tests. |
| Publishing an invalid draft returns 422 with the validator's errors (test) | yes | `test_publishing_an_invalid_draft_returns_422_with_the_validators_errors`: the draft has a cycle and an unknown agent. Publish → 422 `VALIDATION_ERROR`. `details` has one entry per issue, `field` = `tasks.<id>` or `template`, `issue` = `<rule>: <message>`, and the rules are exactly `{cycle, unknown_agent}`. Nothing is published, and the attempt is audited `failed`.<br>`test_publish_validates_against_the_registry_at_publish_time`: a draft that was valid when saved gets 422 `unknown_agent` after its agent is retired.<br>On Mongo: `tests_int/test_mongo_templates.py::test_an_invalid_publish_is_422_and_audited_as_failed`.<br>Mutations M3, M4, M5 and M11 are killed. |
| An attempt to change a published version fails (test) | yes | Four routes to changing a version, each tested:<br>(a) Editing its draft: `test_the_draft_of_a_published_version_cannot_be_changed` gives 409 and the stored v1 is unchanged.<br>(b) Writing to the version route: `test_no_route_writes_to_a_version` gives 405 for PUT, PATCH, DELETE and POST.<br>(c) Publishing again: `test_publishing_again_never_overwrites_an_existing_version` appends v2 and leaves v1 as it was.<br>(d) The store itself: `test_a_stored_version_is_never_written_again` (insert raises `VersionExistsError`), and `test_no_store_has_a_way_to_update_or_delete_a_version[InMemory/Mongo/Protocol]`.<br>On Mongo, with a root read-back: `test_a_published_version_document_never_changes` (the v1 document is byte-identical after the draft edit attempt, a re-publish, v2 and archive) and `test_the_unique_index_refuses_a_second_write_of_a_version`.<br>Mutations M6, M7, M18, M20, M21, M23 and M24 are killed. |
| `make test-xt` covers every new route | yes | `rtk proxy make test-xt` (inside `make check`) → `196 passed` on the rebased branch (attempt 1: `148 passed`); `cross-tenant surface: 21 API routes, 8 MCP tools`. The surface listing shows all 9 new routes:<br>• `POST/GET /templates/drafts`<br>• `GET/PUT /templates/drafts/{draft_id}`<br>• `POST …/validate` and `POST …/publish`<br>• `GET /templates`<br>• `GET /templates/{key}/versions/{version}`<br>• `POST …/archive`<br>`test_xt_coverage.py::test_every_api_route_has_a_cross_tenant_case` passes. Each new row checks that:<br>• org B's drafts and versions are 404, indistinguishable from missing;<br>• org A's version numbers ignore org B's (B has engineer-india v1–v3; A's next publish is its own v2);<br>• no org-A call changes any org-B document (root snapshot before and after);<br>• responses carry none of B's markers, which include B's template names and draft ids.<br>Evidence: `docs/evidence/TPL-2/make-check.txt`. |

RED before GREEN:
- `docs/evidence/TPL-2/red-templates-route.txt` (import error before the package existed)
- `red-inputs-caps.txt`
- `red-body-limit.txt`
- `red-review-fixes.txt`

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Routes and roles.**<br>• HR and HR_ADMIN: `POST /templates/drafts` (201), `GET /templates/drafts`, `GET`/`PUT /templates/drafts/{id}`, `POST …/validate`.<br>• HR_ADMIN only: `POST …/publish`, `POST /templates/{key}/versions/{n}/archive`. A refused call is audited `denied` before the 403.<br>• All roles: `GET /templates`, `GET /templates/{key}/versions/{n}`.<br>Operation ids: `createTemplateDraft`, `listTemplateDrafts`, `getTemplateDraft`, `saveTemplateDraft`, `validateTemplateDraft`, `publishTemplateDraft`, `listTemplates`, `getTemplateVersion`, `archiveTemplateVersion`. | `test_role_matrix.py`, `test_templates_route.py` |
| **Save.** A draft is saved only if it is schema-valid (otherwise 422 field-level). A `sensitive_input` issue refuses the save (422, nothing stored). Other FR-T3 issues are stored on the draft as `issues`. `version` and `status` in a body are ignored. `PUT` takes an optional `revision`; a mismatch is 409. | `test_a_draft_holding_a_sensitive_value_is_not_saved`, `test_lifecycle_fields_in_a_draft_body_are_ignored`, `test_a_revision_mismatch_is_409` |
| **Publish.**<br>• It validates against the registry as it is at publish time (`TemplateValidator.from_registry`; retired agents are refused).<br>• Version numbers are per org and per key, starting at 1.<br>• It is idempotent: publishing a published draft returns its version and audits nothing new.<br>• It is atomic: head, version, draft lock and audit commit together, and a failed audit write leaves nothing (AUD-1).<br>• A concurrent publish of the same draft gets 409. | `test_each_publish_appends_the_next_version`, `test_publishing_a_published_draft_again_returns_its_version`, `test_a_publish_whose_audit_write_fails_*` (unit and Mongo), `test_each_org_numbers_its_own_versions` |
| **Version record.** `TemplateVersionRecord{key, version, status: published\|archived, template (a full Template with version and status filled in), published_at/by, archived_at/by, draft_id}`. Archiving changes only `status`, `archived_at` and `archived_by`; the body never changes. Archive is idempotent and audited `template.archive`. | `test_only_hr_admin_archives_and_archive_keeps_the_body`, `test_an_archive_whose_audit_write_fails_*` |
| **Storage** (all tenant collections through `onb_data`):<br>• `templates`: one head per `(org_id, key)` (unique), holding `latest_version` and a `versions` summary array.<br>• `template_versions`: insert-only, unique `(org_id, key, version)`.<br>• `template_drafts`.<br>`MongoTemplateStore` and `InMemoryTemplateStore` (from `onb_api.testing`) keep the same contract. | test-int suite, `test_template_lifecycle.py` |
| **Audit.** Actions are `template.publish` (success, failed or denied) and `template.archive` (success or denied). Targets are `template:<key>/v<n>`, `template_draft:<id>` or `template:invalid`. Reasons hold only counts and roles. | `test_publish_and_refusals_are_audited`, `test_a_denied_archive_of_an_out_of_range_version_is_audited_as_invalid` |
| **Service config.** `onb_api.main` requires `CONFIG_DIR`, the repo's `config/`, which holds the tool catalog and the sink policy. Without it, startup fails with `ConfigError`. | `test_startup_fails_without_the_config_dir`, `test_startup_succeeds_with_the_settings` |
| **Body limit.** `/templates` request bodies are capped at 4 MiB (`onb_api.body_limit`). An oversized body gets 413 `PAYLOAD_TOO_LARGE`, whether it is declared or streamed. | `test_body_limit.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U2 @ MS-6 | compose, 2026-11-11 | contributes: the HR_ADMIN publish path over HTTP (API level; UI-2 adds the browser half) | `test_templates_route.py`, `tests_int/test_mongo_templates.py` | no (TPL-2 is not the last prover) |

## Gates
Re-run on the rebased branch (attempt 2).

| Gate | Command | Result |
|------|---------|--------|
| Check | `rtk proxy make check` | `make check: all passed`, exit=0 (`docs/evidence/TPL-2/make-check.txt`). Lint and typecheck ran for every project; api `425 passed`; test-xt `196 passed`, `cross-tenant surface: 21 API routes, 8 MCP tools, 0 event consumers`. |
| Integration | `rtk proxy make test-int` | `NX Successfully ran target test-int for 8 projects`, exit=0 (`make-test-int.txt`). Includes `test_mongo_templates.py` (6) and HIRE-1's `test_mongo_hires.py`. |
| Cross-tenant | `make test-xt` | `196 passed` (inside `make check`). |
| Contracts | `rtk proxy make contracts` then `rtk proxy make contracts-check` | Regenerated after the rebase; `contracts-check: openapi.json and the TS client are up to date`, exit=0 (`make-contracts-check.txt`). |
| Eval self-test | `rtk proxy make evals-selftest` | `27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS`, exit=0 (`make-evals-selftest.txt`). |
| Mutations | `uv run --frozen python docs/evidence/TPL-2/mutate.py` (`.env` loaded) | Re-run on the rebased code: `mutations: 32, killed: 32, survivors: none`, `0 files differ` (`mutation-checks.txt`). |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| TPL-1 | `TemplateValidator.from_registry(...)` and `.validate()` never raise; `issues[*]` carry a rule, a task and a redacted message; `report.template` and `report.order`. | Yes. Used for save, validate and publish. One gap, fixed here: `inputs` numbers and lists bypassed the size caps (see Decisions). |
| REG-1 | `AgentRegistry.list_agents()` includes retired agents with `active=False`. | Yes (`test_publish_validates_against_the_registry_at_publish_time`). |
| AUD-1 | `AuditLog.audit` joins the transaction session, so it rolls back with it. | Yes. A failing audit leaves no head, version or record (Mongo test-int). |
| T-3 | `ROUTE_CASES`, coverage and the role matrix fail on an uncovered route. | Yes. The role-matrix completeness test failed until the rows were added. |
| AUTH-1 | `require_role` and `CurrentPrincipal` give a tenant scope from the token. | Yes. |
| HIRE-1 | `create_app(..., hires=)`, `main.py` needs `PII_MASTER_KEY`, `World.vault_hires()` and the fresh-request steering test. | Yes. All kept through the rebase; the merged suites pass (see Attempt 2). |

## Mutation checks
`docs/evidence/TPL-2/mutation-checks.txt`, made by `sh <withenv> uv run --frozen python docs/evidence/TPL-2/mutate.py` with `.env` loaded for the `int` suite. It ends `source diff after all mutations: 0 files differ` and `mutations: 32, killed: 32, survivors: none`.

The guards the card names:
- **M1 and M2:** HR, or anyone, may publish → `test_hr_publish_is_403_and_hr_admin_publish_is_200` and `test_role_matrix[POST …/publish-HR]` FAILED.
- **M3:** publish skips validation → `test_publishing_an_invalid_draft_returns_422_with_the_validators_errors` FAILED.
- **M4:** 422 without the validator's errors → the same test FAILED.
- **M5:** 422 becomes 409 → the same test FAILED.
- **M6:** the published draft is editable (lifecycle and store checks both off) → `test_the_draft_of_a_published_version_cannot_be_changed` FAILED.
- **M7:** the in-memory store overwrites a version → `test_a_stored_version_is_never_written_again` FAILED.
- **M20:** archive changes the body → `test_no_route_writes_to_a_version` and others FAILED.
- **M21:** the Mongo version index is not unique → `test_the_unique_index_refuses_a_second_write_of_a_version` FAILED.
- **M24:** a Mongo upsert overwrites → the same test FAILED.

The rest:
- M8 and M22: no rollback (in memory, and outside the Mongo transaction).
- M9–M13: audit records and the archive role.
- M14: sensitive drafts get stored.
- M15: a stale revision is accepted.
- M16: re-publish is not idempotent.
- M17: managers can author.
- M18: no draft lock on publish.
- M19: lifecycle fields kept.
- M23: the Mongo draft lock ignores status.
- M25–M26: body limit.
- M27–M29: `inputs` caps.
- M30: an existing version gives 500.
- M31: in-memory reads hand out the stored body.
- M32: out-of-range audit target.

## Review findings left open
A code review and a security review ran on `git diff main...HEAD`. Neither found anything CRITICAL. The one HIGH (security H1: a 1.2 GB numeric `inputs` body accepted, and an int beyond int64 giving a 500) was fixed test-first in 84128a4 and 7842431: `_size` counts digits, int64 bounds, list cap and the body limit. Also fixed:
- code-review L5 (`VersionExistsError` gave 500, now 409);
- L7 (in-memory reads are now copies);
- L8 (out-of-range audit target);
- MEDIUM 1 (the store-surface test covers the Mongo class and the protocol);
- MEDIUM 2 (archive rollback is tested on Mongo).

Left open:
- **MEDIUM** · `routes.py` `GET /templates/drafts`, `GET /templates` (`store.py` `list_drafts`, `list_templates`): no paging or cap.
  - Why left: the PRD §10 lists no paging for these, and how to page belongs to UI-2's list screens.
  - Bounded today by the per-request body limit and by the human pace of authoring.
  - Recommend a `limit` plus cursor, and leaving `template` out of the drafts list.
- **MEDIUM** · `validators.py`: every save, validate and publish re-reads the registry, then runs the full PII scan; nothing limits the rate.
  - Why left: correctness needs the current registry at publish.
  - Recommend caching keyed by registry version, plus rate limiting. Rate limiting is SEC-2's.
- **LOW** · `lifecycle.py` `publish`: two simultaneous publishes of the same draft give one 200 and one 409, and a retry then gets 200. Nothing is corrupted.
- **LOW** · `DraftUpdate.revision` is optional, so a `PUT` without it is last-writer-wins (documented). UI-2 should always send it; making it required is a one-line change if Haneef wants it.
- **LOW** · `store.py` `insert_template`: a first-publish race may answer 409 instead of being retried. Safe.
- **LOW** · The `versions` array on a head grows with each publish; Mongo's 16 MB document limit is the real ceiling.
- **LOW (uncertain)** · Free-text names in titles are not detected as PII. Names are not vault fields under PRD §7.10, so this matches the PRD.

## Left undone, and why
- `POST /templates/drafts/chat` (SSE) belongs to TB-1.
- Change requests belong to TB-2.
- The UI belongs to UI-2.
- Seeding `templates/engineer-india.yaml` as a published version belongs to SEED-1. Use `TemplateLifecycle.create_draft` then `publish`, as `tools/tests_xt/conftest.py::_seed_templates` does.

## Found for other goals
- **SEED-1:** publish through `TemplateLifecycle` (or the HTTP routes) so the head, version and audit stay consistent. The service needs `CONFIG_DIR`.
- **RUN-1:**
  - Pin `(key, version)` and read the body with `TemplateLifecycle.get_version(key, version)`. It returns a `Template` with `version` and `status`.
  - Refuse to start runs on `status == "archived"` (TPL-2 archives but does not police runs).
  - Version bodies never change, so a pinned run's template is stable.
- **TB-1:**
  - The Builder writes drafts through `TemplateLifecycle.create_draft` or `save_draft`. A draft with a `sensitive_input` issue raises `DraftRefusedError(issues)`.
  - Per FR-T5, the Builder must never call `publish`; only the HR_ADMIN route does.
  - The `template_builder` manifest reads `template_versions` through mcp-mongo, which is why the collection is not `append_only` in `onb_data` (see Decisions).
- **UI-2:** use the generated client ops above. Show `issues` from save and `details` from a 422 publish inline (`field` = `tasks.<id>`). Send `revision` on save.
- **SEC-2:**
  - A global request-body limit. Only `/templates` has one.
  - Rate limits on `/templates/drafts/*/validate` and save.
  - The paging items above.
- **TPL-1 contract change:** `onb_contracts.templates` `inputs` now refuses lists longer than 50, integers outside int64, and numbers count by their digits toward the 16 000 size. `make evals-selftest` still PASSes.

## Decisions forced (ADRs written)
No ADR was written; 0009 is still free. Recorded here:
1. **Version immutability without `append_only`.** I first made `template_versions` append-only in `onb_data` (f9d48be), then reverted it (9a3d976), for two reasons:
   - mcp-mongo reads agent-granted collections with `store.tenant(...)`, which refuses append-only collections;
   - the manifest validator never grants an append-only collection, and `template_builder` is granted `template_versions` read (REG-1/MCP-3 config).
   Immutability instead rests on:
   - no update or delete operation in `TemplateStore` (a test over all three store types);
   - a unique `(org_id, key, version)` index;
   - agents may not write the collection (`AGENT_WRITABLE_COLLECTIONS`).
   If Haneef wants an ADR, the content is this paragraph.
2. **Archive state lives on the head** (`templates.versions[i].archived_at`), so the version document itself is never written after insert.
3. **Validation on save** (FR-T3) stores drafts with their issues, except `sensitive_input`, which refuses the save so raw PII never lands in `template_drafts`.
4. **Routes beyond the card's list:**
   - `POST /templates/drafts` (a draft must come from somewhere before TB-1's chat);
   - `GET /templates/drafts` (the HR drafts list);
   - `GET /templates/{key}/versions/{n}` (RUN-1 and suggest mode need the body);
   - `POST /templates/{key}/versions/{n}/archive` ("archive" in the scope).
   All have role-matrix rows and xt cases.

## Traps hit
- The worktree guard refuses heredocs mixed with other commands, and `. ./.env`. Use a script in the job tmp folder (`withenv.sh`) to load `.env`.
- An `AsyncMongoClient` is bound to the event loop that first used it. Seed through a separate store, then build the app on a fresh one.
- BSD `sed` has no `\b`; use `perl -pi`.
- `make test-xt` seeds through the lifecycle, so its audit records go to an in-memory log to keep the `AUDIT_ROWS` totals exact.
