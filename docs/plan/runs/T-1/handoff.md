# Handoff — T-1 · Make tenant scoping impossible to forget        attempt 1 · goal/t-1-tenant-repository · see `git log -1` (evidence captured at f65865d)

**Outcome claimed.** `libs/py-data` (`onb_data`) is the only path to MongoDB:

- `connect(uri, database)` returns a `DataStore` that hands out repositories only.
- `store.tenant(name)` returns a `TenantRepository`. It adds `org_id` to every filter, sets it on every insert, refuses updates that touch it, sets it on upsert, and puts `$match: {org_id}` first in every pipeline. It raises `NoTenantContextError` before reaching the driver when there is no `tenant_scope(org_id)`.
- By-id operations are `{_id, org_id}` and raise `NotFound` for foreign ids.
- `store.shared(name)` serves only `orgs`, `agent_registry` and `tool_catalog`.
- `store.ensure_index` requires an `org_id` prefix on tenant collections.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Every repository operation raises without a context, and with one sees only its org (two-org test against testcontainers Mongo) | yes | `docs/evidence/T-1/two-org-integration.txt`: `uv run --frozen pytest libs/py-data/tests_int -v` gives `24 passed`, exit=0. The live tests seed org_alpha and org_beta in a scratch database:<br>• `test_every_operation_raises_without_a_context_and_changes_nothing[<op>]` covers all 12 operations, and the data is unchanged afterwards (read back as root).<br>• `test_the_operation_list_matches_the_repository` fails if a new public operation is not in that list.<br>• "Sees only its org": `test_reads_see_only_the_own_org`, `test_an_aggregate_starting_with_sort_and_group_sees_only_the_own_org`, `test_writes_land_in_the_own_org_only`, `test_an_upsert_creates_the_document_in_the_own_org`.<br>• Unit side: `test_every_operation_raises_without_a_tenant_context[*]` asserts the driver received no call.<br>The Mongo is the compose `mongo:8` container (Docker), not the `testcontainers` library. See "Decisions forced". |
| A foreign `_id` lookup raises `NotFound`, not a permission error (test) | yes | Live: `test_a_foreign_id_is_not_found_for_get_update_and_delete`. `get`, `update_by_id` and `delete_by_id` on org_beta's `b1` from org_alpha each raise `NotFound`, none is a `PermissionError`, and `b1` is unchanged. Unit: `test_a_foreign_id_raises_not_found_not_a_permission_error` asserts `not issubclass(NotFound, PermissionError)`, `NotFound is NotFoundError`, and no org id in the message. Both pass in `two-org-integration.txt` and `py-data-coverage.txt`. |
| Mutation check: removing the `$match` prepend fails a named test (output recorded) | yes | `docs/evidence/T-1/mutation-checks.txt`, M1: `return [{"$match": …}, *stages]` → `return stages`. These failed: `test_aggregate_prepends_org_match_before_a_leading_sort`, `test_aggregate_prepends_org_match`, `test_aggregate_of_an_empty_pipeline_is_still_scoped` and `test_aggregate_sends_plain_lists_and_dicts_to_the_driver` (4 failed), and live `test_reads_see_only_the_own_org` and `test_an_aggregate_starting_with_sort_and_group_sees_only_the_own_org` (2 failed). Restored with 0 files differ. M2 (`$match` appended last, after a leading `$sort`) fails the first two unit tests. The script is `docs/evidence/T-1/mutate.sh`. |
| A test asserts `py-data` exports no `Collection` or `Database` object | yes | `libs/py-data/tests/test_exports.py` walks `onb_data` and every submodule (`pkgutil.walk_packages`). It checks the public attributes of each module, `__all__`, a real `connect()` store, and both repository kinds. It asserts none is an instance or subclass of `AsyncCollection`, `AsyncDatabase`, `AsyncMongoClient`, `Collection`, `Database` or `MongoClient`. `test_the_forbidden_check_recognises_pymongo_objects` proves the detector works. Mutation-checked: M14 (runtime import of `AsyncCollection` in `repository.py`) fails `test_no_module_exposes_a_collection_database_or_client[onb_data.repository]`, and M15 (public `DataStore.database`) fails `test_a_connected_store_exposes_no_collection_database_or_client`. |
| `libs/py-data` coverage ≥ 80% | yes | `docs/evidence/T-1/py-data-coverage.txt`: `nx run py-data:test` (with `--cov-fail-under=80`) gives `TOTAL 316 0 100%`, `Required test coverage of 80% reached. Total coverage: 100.00%`, `144 passed`. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `from onb_data import connect, tenant_scope`. Then `store = connect(uri, db)` and `with tenant_scope(org_id): await store.tenant("hires").find(...)`. The URI comes from configuration. The client is lazy and created only in `connect()`. | `test_connect_builds_a_lazy_store_without_touching_the_network`, live tests |
| `tenant_scope` is a sync context manager over a contextvar. It works inside `async def`, nests and restores, and resets on exceptions. asyncio tasks each keep their own tenant. `current_tenant()` raises `NoTenantContextError`, which AUTH-1 should map to 401. | `tests/test_context.py` |
| Org ids must match `[A-Za-z0-9_-]{1,128}`, or `ValueError` is raised. | `test_an_invalid_org_id_is_refused` |
| Repository operations: `find`, `find_one`, `get`, `insert_one`, `insert_many`, `update_one(upsert=)`, `update_many`, `update_by_id`, `delete_one`, `delete_by_id`, `aggregate`, `count`. Results are plain dicts. `update_*` returns `UpdateOutcome(matched, modified, upserted_id)`. | `test_every_public_operation_is_covered` |
| A filter or document may carry `org_id` only if it equals the context org; otherwise `TenantViolationError`. Updates may never touch `org_id` (`$set`, `$unset`, `$rename` in either direction, `$setOnInsert`, dotted paths). Only operator updates are accepted: no replacement documents or update pipelines. | `test_a_filter_naming_another_org_is_refused`, `test_an_update_that_touches_org_id_is_refused[*]`, `test_only_operator_updates_are_accepted[*]` |
| Pipelines refuse `$lookup`, `$graphLookup`, `$unionWith`, `$out` and `$merge` at any depth, including tuple sub-pipelines. Tenant pipelines also refuse first-stage-only stages (`$geoNear`, `$search`, `$vectorSearch`, `$documents`, `$changeStream`, …). | `test_aggregate_refuses_stages_that_read_or_write_other_collections[*]`, `test_aggregate_refuses_first_stage_only_operators_it_cannot_scope[*]` |
| Inputs are deep-copied into plain dicts and lists with exact-`str` keys before checks, and that copy is what is sent. The caller's objects are never mutated. | `test_keys_must_be_plain_strings[*]`, `test_insert_sets_org_id_without_mutating_the_caller_document`, `test_aggregate_sends_plain_lists_and_dicts_to_the_driver` |
| By-id operations refuse mapping, list, tuple and set ids (`TypeError`). | `test_by_id_operations_refuse_an_operator_or_collection_as_the_id[*]` |
| `GLOBAL_COLLECTIONS == {orgs, agent_registry, tool_catalog}`. `tenant()` refuses those names and `shared()` refuses all others (`UnknownCollectionError`). Global repositories need no context and add no `org_id`. | `tests/test_store.py` |
| `await store.ensure_index(coll, [("org_id", 1), ...], unique=True)`. Tenant indexes must start with `org_id` (`InvalidIndexError`), so unique indexes are per org. | `test_a_tenant_index_must_start_with_org_id`, live `test_an_org_prefixed_unique_index_allows_the_same_value_in_two_orgs` |
| `connect(..., event_listeners=[...])` passes listeners to the PyMongo client. This is T-2's hook. | `test_connect_passes_event_listeners_to_the_client` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show T-1` lists no ledger rows for T-1 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. Lint, typecheck and test pass for 17 projects (`docs/evidence/T-1/make-check.txt`). |
| Integration | `make test-int` | exit=0. Stack checks `12 passed`, py-data `24 passed`, then `Successfully ran target test-int for project py-data` (`docs/evidence/T-1/make-test-int.txt`). |

Run on compose project `ob-t-1` (Caddy on 127.0.0.1:18200/18201), macOS Docker Engine 29.8.2. The other gates (test-xt, contracts, evals, PII, e2e) do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-2 | `make test-int` runs `make up`, sources `.env`, and fans out to `nx run-many -t test-int` | yes. The new tests ran under py-data's existing `test-int` target. |
| F-2 | The `root_client` and `mongo_tunnel` fixtures in `libs/py-data/tests_int/conftest.py` | yes. The live tests build a root URI from `root_client.address` plus the env credentials. |
| F-2 | pymongo 4.19 is a py-data dependency | yes. `AsyncMongoClient` is used, with no new dependency and no `uv.lock` change. |
| F-2 | Replica set `rs0`, auth required | yes. `test_server_is_a_replica_set_primary` still passes. |

## Mutation checks
Full output is in `docs/evidence/T-1/mutation-checks.txt`, produced by `docs/evidence/T-1/mutate.sh`. After each check the file was restored ("0 files differ"), and the final line is `worktree diff after all mutations: 0 files differ`.
- M1 (box 3): no `$match` prepend. 4 unit tests failed, including `test_aggregate_prepends_org_match_before_a_leading_sort`, and 2 live tests.
- M2: `$match` appended last. `test_aggregate_prepends_org_match` and `…_before_a_leading_sort` FAILED.
- M3: filters not scoped. 8 unit and 3 live tests failed, including `test_reads_see_only_the_own_org`.
- M4: no context falls back to a default org. 15 unit tests failed (including `test_every_operation_raises_without_a_tenant_context[*]`) and 12 live tests (`test_every_operation_raises_without_a_context_and_changes_nothing[*]`).
- M5: `NotFoundError` subclasses `PermissionError`. `test_a_foreign_id_raises_not_found_not_a_permission_error` and the live `test_a_foreign_id_is_not_found_for_get_update_and_delete` FAILED.
- M6: `get` returns `None` for a foreign id. The same two tests and `test_a_global_lookup_of_a_missing_id_raises_not_found` FAILED.
- M7: upsert without `$setOnInsert`. `test_an_upsert_sets_org_id_on_insert` and `test_an_upsert_keeps_the_caller_set_on_insert_fields` FAILED. The live tests still pass, because MongoDB copies `org_id` from the equality filter into the upserted document. The `$setOnInsert` is a second guard.
- M8: updates may touch `org_id`. 8 cases of `test_an_update_that_touches_org_id_is_refused` FAILED.
- M9: inserts unscoped. 3 unit and 2 live tests failed (`test_writes_land_in_the_own_org_only`, `test_an_org_prefixed_unique_index_…`).
- M10: a filter may name another org. `test_a_filter_naming_another_org_is_refused` and `test_a_filter_with_an_operator_on_org_id_is_refused` FAILED.
- M11: cross-collection stages allowed. 8 cases and `test_global_pipelines_still_refuse_cross_collection_stages` FAILED.
- M12: no `org_id` index prefix required. `test_a_tenant_index_must_start_with_org_id` FAILED.
- M13: a global collection handed out as a tenant one. 3 cases of `test_a_global_collection_is_never_handed_out_as_a_tenant_one` FAILED.
- M14 and M15 (box 4): see above.
- M16: tuples not normalised. Both tuple `$facet` cases and `test_aggregate_sends_plain_lists_and_dicts_to_the_driver` FAILED.
- M17: operator ids accepted. 12 cases of `test_by_id_operations_refuse_an_operator_or_collection_as_the_id` FAILED.
- M18: `str`-subclass keys accepted. 4 cases of `test_keys_must_be_plain_strings` FAILED.
- M19: first-stage-only stages pass through. 4 cases of `test_aggregate_refuses_first_stage_only_operators_it_cannot_scope` FAILED.

RED before GREEN:
- `docs/evidence/T-1/red-unit-tests.txt`: tests were written before any API existed, so collection failed with an ImportError.
- `docs/evidence/T-1/red-review-fixes.txt`: 25 tests failed before the review fixes.

## Review findings left open
I ran a code review and a security review: two general-purpose agents, each with a reviewer brief, on `git diff main...HEAD`.

Fixed:
- **High** (both reviews): a tuple sub-pipeline inside `$facet` skipped the cross-collection check, letting `$unionWith`/`$lookup` read every org.
- **Low/Medium:** `str`-subclass keys hid `org_id`.
- **Low/Medium:** operator ids in by-id operations.
- **Medium:** first-stage-only stages now get a clear refusal.
- **Low:** `connect()` now checks the database name before creating a client, and has the `event_listeners` hook.

Still open:
- **high (by the code review) · `repository.py`/`store.py` · No session or transaction support.** The EVT-1 transactional outbox ("domain change + event in one transaction") cannot be written through `onb_data` yet. Left because it is outside T-1's card scope (the card lists find, by-id, insert, update, aggregate and count). This is an API extension EVT-1 owns, not a scoping defect. Suggested shape: a `DataStore.transaction()` async context manager that keeps the session in a contextvar, which every repository call passes as `session=`. See "Found for other goals".
- **high (by the code review) · `repository.py` · No `find_one_and_update` and no cross-org job claim.** EVT-1 workers must lease jobs before knowing the tenant, and must dead-letter jobs that have no `org_id`. Left for the same reason: it needs a design decision, either a narrow audited system queue or `jobs`/`dead_letters` as deliberate global collections. That decision belongs to EVT-1, and it changes `GLOBAL_COLLECTIONS`, which this card fixes at three names.
- **medium · `repository.py` insert/upsert · A caller-chosen `_id` can show that the id exists in another org.** The `_id` index is unique across the whole collection, so inserting org B's `_id` from org A raises `DuplicateKeyError`. No data crosses orgs. Left because the fix is a convention, not a check: tenant documents should use unguessable server-generated ids (ObjectId/UUID). Blocking caller-supplied `_id` would break idempotent writes that EVT-1 may need. Recorded for AUTH-1, HIRE goals and EVT-1.
- **medium · `repository.py:find/aggregate` · `to_list()` is unbounded.** Callers should pass `limit`. A configured maximum belongs to the API or MCP goals that expose lists.
- **low · `context.py` · A nested `tenant_scope` may switch org.** This is deliberate for now, and `test_nested_scope_restores_the_outer_tenant` covers it. AUTH-1 or a lint rule can restrict `tenant_scope` to the token dependency and the job consumer.
- **low · `store.py` · Any name off the global list becomes a tenant collection, so a typo creates a new collection.** An explicit tenant collection list can come with the schemas.
- **low · raw handles stay reachable through underscore attributes** (`repo._collection`, `store._database`). This is a Python convention. T-2's command listener is the runtime safety net, and an import or lint rule could forbid `pymongo` imports outside `libs/py-data`.
- **low · `tests/fakes.py` accepts any keyword arguments.** Covered by the live tests, which run `find`, `aggregate` and upsert against real Mongo.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal T-1`.
- Transactions and job leasing: see the open findings, for EVT-1.

## Found for other goals
- **EVT-1:** add session/transaction support to `onb_data` (`DataStore.transaction()` plus `session=` threaded through the repository) and `find_one_and_update`. Decide how workers claim jobs across orgs and dead-letter jobs that have no `org_id`. A narrow system repository is one option. Making `jobs`/`dead_letters` global is another, which changes `GLOBAL_COLLECTIONS` and the T-1 test that pins it.
- **T-2:** attach the command listener with `connect(..., event_listeners=[listener])`. The listener should also check that the leading `$match` on `org_id` is present and walk nested pipelines for `from`/`coll`. Consider a lint or import rule that forbids `pymongo` imports outside `libs/py-data`. Integration tests run as Mongo root, and a least-privilege app user would make the safety net meaningful.
- **AUTH-1:** map `NoTenantContextError` → 401 and `NotFound` → 404, and `TenantViolationError` → 400 (or 403 for an attempt to choose an org). Open `tenant_scope(claims.org_id)` in a yield dependency.
- **HUB / Mongo MCP (PRD §7.5):** use an allowlist of stages and operators for agent input rather than this layer's denylist.
- **All tenant schemas:** use unguessable `_id`s (ObjectId/UUID) for tenant documents (see the medium finding). Pass `limit` on list reads.
- **MASTER-PLAN §7 / GOALS T-1 box 1:** the wording says "testcontainers Mongo", but the integration gate runs on the compose Mongo (F-2 recorded the same thing). Consider rewording it to "throw-away Docker Mongo".

## Decisions forced (ADRs written)
None needed an ADR:
- **Box 1 "testcontainers Mongo".** I used F-2's compose Mongo, reached through the existing loopback tunnel, with a scratch database per test that is dropped afterwards. PRD §7.5 says "throw-away MongoDB container (single-node replica set via testcontainers / Docker)", so Docker satisfies the PRD. The `testcontainers` library is not in `uv.lock`, and `make test-int` is defined over the compose stack. Adding a second Mongo harness would have meant a new dependency and a root `uv.lock` change outside this card's scope.
- **Error names.** Ruff N818 requires an `Error` suffix, so the errors are `NoTenantContextError`, `NotFoundError`, `TenantViolationError`, `InvalidIndexError` and `UnknownCollectionError`. The card's `NotFound` is exported as an alias of `NotFoundError` (the same class). There is no `noqa`.
- **No pytest-asyncio.** Async tests use `asyncio.run`, which avoids a new dev dependency.

## Traps hit
- Card trap "`$match` must be first even when the caller passes `$sort` first": covered by `test_aggregate_prepends_org_match_before_a_leading_sort` and mutation M2.
- Card trap "an upsert must set `org_id`": handled by `$setOnInsert`. MongoDB also sets it from the equality filter, so only the unit test catches its removal (M7).
- A unique `{org_id, email_hash}` index on the seeded `hires` collection failed because the seeds have no `email_hash`, giving duplicate nulls. The live index test uses its own `people` collection.
- The session's shell hook rewrites `find`/`grep` output and refuses heredocs and `.` sourcing in commands. Scripts live in the job temp directory and run via `bash <file>`; `mutate.sh` is copied into the evidence.
