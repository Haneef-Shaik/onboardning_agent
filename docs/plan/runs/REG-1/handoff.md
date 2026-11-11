# Handoff — REG-1 · Declare every agent's allowed tools in a versioned registry        attempt 2 · goal/reg-1-agent-registry · 83adf43 (rebased by the orchestrator onto main with T-2)

**Outcome claimed.** Each of the six agents has a validated manifest in `config/agents/`. Syncing stores the manifests in the global `agent_registry` collection with a version that goes up on every change. Each change gets an audit record in every org, and HR_ADMIN reads the registry through `GET /agents` and `GET /agents/{name}`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A manifest listing a vault collection is rejected (test) | yes | `libs/py-agents/tests/test_manifest.py::test_a_manifest_listing_a_vault_collection_to_read_is_rejected[*]`, `…_to_write_is_rejected[*]`, `test_a_vault_name_anywhere_in_the_manifest_is_rejected[*]`; `test_review_fixes.py::test_a_vault_name_as_yaml_binary_is_rejected[*]`, `test_a_name_with_the_vault_inside_it_is_rejected[*]`. Command `uv run --frozen pytest libs/py-agents/tests -q -k "vault_collection or vault_name or yaml_binary"` → `20 passed, 95 deselected` |
| Changing a YAML and re-syncing bumps the version and writes an audit record (test) | yes | `libs/py-agents/tests/test_sync.py::test_changing_a_yaml_and_resyncing_bumps_the_version_and_writes_an_audit_record` (`-k bumps_the_version` → `1 passed`). Live on compose Mongo: `apps/api/tests_int/test_mongo_registry.py::test_changing_a_yaml_and_resyncing_bumps_the_version_and_audits_it_in_mongo` (`nx run api:test-int` → `27 passed` on the rebased branch, under the T-2 safety net) |
| All six manifests validate, and the catalog matches `evals/fixtures/seed_v1.json` `action_catalog` (test) | yes | `libs/py-agents/tests/test_config_manifests.py::test_all_six_manifests_validate`, `::test_the_catalog_matches_the_eval_fixture_action_catalog` → `2 passed, 113 deselected` |
| `GET /agents` is HR_ADMIN only (role test) | yes | `apps/api/tests/test_role_matrix.py::test_role_matrix[*-GET /agents]` and `[*-GET /agents/{name}]`, `apps/api/tests/test_agents_route.py::test_the_registry_is_hr_admin_only[*]`, `::test_hr_and_managers_are_refused_before_the_registry_is_read`. Command `uv run --frozen pytest -q apps/api/tests/test_agents_route.py apps/api/tests/test_role_matrix.py -k agents` → `26 passed, 26 deselected`. Live: `test_mongo_registry.py::test_get_agents_is_hr_admin_only_against_the_real_registry[*]` |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `from onb_agents.registry import load_manifests, load_catalog, parse_manifest, AgentRegistry`. `AgentRegistry(MongoRegistryStore(store), AuditLog(store))`; `await registry.get_agent(name)` returns a `RegisteredAgent(name, version, sha256, active, manifest, updated_at)` or None; `list_agents()` returns every agent by name, retired ones included. | `test_sync.py`, `test_mongo_registry.py` |
| **HUB-1:** a record is checked again when read. It must parse as a manifest, and its `sha256` and `_id` must match, or `ManifestError` is raised. Treat that as a denial, and check `active` too: a retired agent grants nothing. | `test_a_tampered_stored_manifest_does_not_load`, `test_review_fixes.py::test_a_stored_manifest_widened_behind_the_registry_does_not_load`, `::…under_another_name…` |
| The manifest shape follows PRD §7.3. `mcp: {server: {tools, collections?: {read, write}}}`. Collections are granted only on the `mongo` server. Reads must be in `AGENT_READABLE_COLLECTIONS` and writes in `AGENT_WRITABLE_COLLECTIONS`; these equal `onb_mcp_mongo.db_users` READ_ONLY/READ_WRITE, and a test keeps them equal. `write ⊆ read`. Mongo tools come from §7.5. `requires_approval` is `tool` or `tool:collection` (a Mongo write tool on a write collection). | `test_manifest.py`, `test_review_fixes.py::test_the_allowlists_equal_the_mcp_database_users_sets` |
| No string in a manifest can contain `pii_vault`, `org_keys` or `pii_access_log`, in any case. Strings are strict, so a YAML `!!binary` value or a number is refused. The check runs before and after parsing. | box 1 tests, mutations M1, M2, M16–M18 |
| `manifest.llm` has the same fields as `onb_llm.LLMSelection` (`alias`/`connector`/`model`/`params`). `params` is a typed `LLMParams` (temperature, max_tokens ≤ `limits.max_tokens`, top_p, stop); give `onb_llm` `manifest.llm.params.values()`. | `test_config_manifests.py::test_the_manifest_llm_has_the_shape_of_the_llm_selection`, `test_review_fixes.py::test_llm_params_*` |
| Sync semantics: a new agent is registered at v1. A changed manifest is a new version. A removed file retires the agent (new version, `active: false`). An unchanged manifest writes nothing. Audit actions are `registry.agent_register`, `registry.agent_update`, `registry.agent_retire` and `registry.catalog_update`. The target is `agent:<name>/v<n>` or `catalog:tool_catalog/v<n>` and the reason is `sha256:<16 hex>`. Each change is audited in every org in `orgs`. A sync with no orgs is refused. Writes and audit records run in one transaction, so a failed audit stores nothing. A concurrent change gives `RegistrySyncError`. | `test_sync.py`, `test_mongo_registry.py::test_a_failed_audit_write_rolls_back_the_registry_and_the_audit_records` |
| **SEED-1:** run `uv run python -m onb_agents.registry sync --actor <HR_ADMIN user id>` after seeding `orgs`. It reads `MONGO_URI` and `MONGO_DATABASE` from the environment. | `test_mongo_registry.py::test_the_sync_command_writes_the_registry` |
| The tool catalog is stored as one document, `tool_catalog/_id: "tool_catalog"`, holding `{version, sha256, actions: [{action, agent, task_type?}]}`. It equals the fixture's `action_catalog`. | box 3 |
| API: `create_app(..., agents=AgentRegistryReader \| None)`; without one, `/agents*` answers 503. `AgentRecord` and `AgentsEnvelope`/`AgentEnvelope` live in `onb_contracts.agents`; the TS client is regenerated (`listAgents`, `getAgent`). | `test_agents_route.py`, `make contracts-check` |
| `test_role_matrix.py` now has `SAMPLE_PATHS`, and every route with a path parameter needs a concrete sample there (enforced by `test_every_path_parameter_route_has_a_sample_path`). | that test |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U8 @ MS-5 | compose, 2026-11-10 | contributes: HR_ADMIN reads each agent's allowed tools | `test_agents_route.py`, `test_mongo_registry.py` | no (MS-5 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed` (lint/typecheck/test for 17 projects; py-agents 97%) |
| Integration | `make test-int` | `NX Successfully ran target test-int for 7 projects` (api 27 passed, py-data 49, mcp-mongo 79, py-eventbus 19, py-crypto 12, py-telemetry 5, py-evals 4; tools 12) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Eval self-test | `make evals-selftest` | `result: PASS` (`evr_20261111T044252Z_7162b985`, 27 cases x 3, controls caught 8/8) |
| Cross-tenant | `make test-xt` | not available yet (T-3). There is a cross-tenant case in `test_agents_route.py::test_both_orgs_admins_see_the_same_global_registry_and_no_org_data` |

Full result lines: `docs/evidence/REG-1/gates.md`.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `store.shared(...)` serves `agent_registry`, `tool_catalog` and `orgs` unscoped; operator updates only | yes (`MongoRegistryStore`, live test) |
| T-1 | `store.run_transaction` joins every repository call in the callback | yes: a failed audit rolls back the registry and the audit records (live) |
| AUD-1 | `AuditLog.audit` in `tenant_scope`, fails closed, joins the transaction | yes |
| AUD-1 | `InMemoryAuditLog` refuses the same input | yes (used in unit tests) |
| C-1 | envelope subclasses, `respond()`, `AppError`, `make contracts` | yes |
| T-2 | a root read across orgs in an integration test goes in `with unscoped("reason"):`; product code must be scoped | yes: the registry's product code made no flagged command (trace below) |
| AUTH-1 | `require_role` runs before other dependencies and data reads | yes (`test_hr_and_managers_are_refused_before_the_registry_is_read`) |

## Mutation checks
22 of 22 mutations were caught. Full output: `docs/evidence/REG-1/mutation-checks.txt`. Scripts: `mutate.py` and `mutate2.py` in the same folder.
- M1: both vault validators turned off → `test_a_manifest_listing_a_vault_collection_to_read_is_rejected[pii_vault]` and 9 more failed.
- M2: case-sensitive match → `…_to_read_is_rejected[PII_VAULT]` failed.
- M4: `users` allowlisted and removed from the never-grantable set → `test_an_invalid_manifest_is_rejected_with_the_reason[…users]` failed.
- M5: no version bump → `test_changing_a_yaml_and_resyncing_bumps_the_version_and_writes_an_audit_record` failed.
- M6: no audit → the same test and 5 more failed.
- M7: audited in one org only → the first-sync, change and catalog tests failed.
- M8: no-org check removed → `test_a_change_with_no_org_to_audit_in_is_refused_and_stores_nothing` failed.
- M9: writes outside the transaction → `test_a_failed_audit_write_stores_no_registry_change` failed.
- M10: no re-validation on read → `test_a_tampered_stored_manifest_does_not_load` failed.
- M11: no retire → `test_a_removed_manifest_retires_the_agent_and_a_restored_one_comes_back` failed.
- M12: the version filter dropped → `test_a_replace_is_checked_against_the_stored_version` failed.
- M13: HR allowed → `test_the_registry_is_hr_admin_only[hr-/agents]` failed.
- M14: catalog drift → `test_the_catalog_matches_the_eval_fixture_action_catalog` failed.
- M15: unknown agent accepted → `test_a_bad_catalog_is_refused[…]` failed.
- M16: all three binary-smuggling layers off → `test_a_vault_name_as_yaml_binary_is_rejected[b3JnX2tleXM=]` failed.
- M17: strict strings off → `test_a_collection_that_is_not_text_is_rejected[run_tasks]` failed.
- M18: prefix-only match → `test_a_name_with_the_vault_inside_it_is_rejected[*]` failed.
- M19: read allowlist off → `…cannot_be_read[checkpoints]` failed.
- M20: write allowlist off → `…cannot_be_written[approvals]` failed.
- M21: params may exceed the limits → `test_llm_params_are_typed_and_bounded[params3]` failed.
- M22: hash not checked → `test_a_stored_manifest_widened_behind_the_registry_does_not_load` failed.
- M23: lost insert race ignored → `test_a_concurrent_first_registration_is_refused_cleanly` failed.

## Review findings left open
Two independent reviews ran: a code review and a security review. Fixed:
- the CRITICAL `!!binary` vault bypass;
- the HIGH denylist-only collections;
- MEDIUM untyped and mutable LLM params;
- LOW: no hash or `_id` check on read, the insert race raising a driver error, and prefix-only vault matching.

Left open:
- **MEDIUM** · `manifest.py` (`mcp` keys, non-Mongo tools, `tools`, `memory_scopes`): these are checked by grammar only, because no server, tool or memory-scope registry exists yet (MOCK-1, NOTIF-1, AG-1). The hub must deny any server or tool it does not serve.
- **MEDIUM** · `registry/__main__.py`: `--actor` is not checked against `users` and does not have to be HR_ADMIN. The same user id is also written into every org's audit log, so org_globex's log names an acme user id. Ids are not PII. The fix needs a system actor type or a users lookup (see Found for other goals).
- **MEDIUM (HUB-1)**: `aggregate` pipelines (`$lookup`, `$unionWith`, `$out`, `$merge`) and `list_collections` must be checked against the manifest by the hub and the MCP server. T-1's repository already refuses the cross-collection stages.
- **LOW** · `sync.py` `list_agents`: one invalid stored record makes the whole `GET /agents` answer 500. This fails closed, but it hides the valid agents too.
- **LOW** · `__main__.py`: PyMongo connection errors are not caught. The CLI prints a traceback, which can include the host but not the credentials.
- **LOW** · `catalog.py`: the catalog checks that the named agent exists, not that the agent holds a tool fitting the action.

## Left undone, and why
- A version history of manifests is not stored. Each version's hash is in the audit record, and the registry keeps only the current manifest. The card asks for "versioned" and "audited on change", which this meets.
- `make test-xt` does not exist yet (T-3).

## Found for other goals
- **AUD-1 follow-up or SEC-1:** add a `system` actor type, so registry syncs and other config-driven changes need no human user id in every org's log.
- **HUB-1:** use `AgentRegistry.get_agent` and deny on `None`, `not active` or `ManifestError`. Check pipelines and `list_collections` against the manifest. Deny unknown servers and tools.
- **SEED-1:** seed `orgs`, then run the sync command. Running it before `orgs` exists is refused.
- **NOTIF-1 / MOCK-1:** the manifests name `notify.notify_send` (the eval cases use `notify_send`; the NOTIF-1 card says `notify`) and `mocks.create_bitbucket_account` / `mocks.provision_laptop`. Rename the manifests if those goals choose other server or tool names. The version bump is the point.
- **AG-1:** `config/agents/*.yaml` `limits` and `tools` (`date_calc`, `render`) are the budgets and local tools to enforce.

## Decisions forced (ADRs written)
None. No ADR number was used, so 0008 is still free. Decisions recorded here:
1. The registry owns `version`, so a manifest file carries none (`version:` in a YAML is refused). The PRD §7.3 example shows the stored form.
2. The PRD §7.3 example has `requires_approval: [update_one:new_hires]` while `new_hires` is not in its `write` list. I made approvals apply only to collections already writable, because an approval rule must not widen a grant (§5.2). So `db_agent` writes `run_tasks` and `documents_meta` only (eval case db-005), with no approval rules. This is a conflict with the PRD example, reported here and not built.
3. The registry is global and the audit log is per org, so each change is audited in every org.

## Attempt 2 (after the rebase onto T-2)
The orchestrator rebased the branch onto `main` with T-2. Two integration tests then failed with `UnscopedCommandError`:
- `test_changing_a_yaml_and_resyncing_bumps_the_version_and_audits_it_in_mongo`: an unscoped `find` on `audit_log`.
- `test_a_failed_audit_write_rolls_back_the_registry_and_the_audit_records`: an unscoped `aggregate` (`count_documents`) on `audit_log`.

I traced both with `docs/evidence/T-2/trace_violations.py`, pointed at this worktree. Output:
`aggregate audit_log: product=-  test=apps/api/tests_int/test_mongo_registry.py:125` and
`find audit_log: product=-  test=apps/api/tests_int/test_mongo_registry.py:105`.
Both come from test code: root read-backs that look across both orgs on purpose, to prove a change was audited in every org and that a rollback left no record in any org.
No product frame was involved. The registry sync and `GET /agents` never read `audit_log`; they only write it through `AuditLog.audit` inside `tenant_scope`.

Each read-back is now in a named `unscoped("…")` block (commit `83adf43`). Assertions are unchanged and the net is untouched.
A re-trace of the whole API integration suite records no violation call sites (`27 passed`).

## Traps hit
- `rtk` truncates `make` output. Run gates as `rtk proxy make …`.
- `test_role_matrix.py` requested `/agents/{name}` literally, so I added `SAMPLE_PATHS`.
- Pydantic's lax mode turned YAML `!!binary` bytes into `str`, which got past the vault scan before parsing. Strict strings and a scan after parsing close it.
