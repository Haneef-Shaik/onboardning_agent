# Handoff — MCP-3 · Back the policy with least-privilege database users        attempt 1 · goal/mcp-3-least-privilege-db-users · see `git log -1` (code at d38aece)

**Outcome claimed.** mcp-mongo reaches MongoDB as two least-privilege users. `mcp_mongo_ro` (role
`mcp_mongo_read`) may only `find` on 10 named tenant collections. `mcp_mongo_rw` (role
`mcp_mongo_write`) may only `find`, `insert` and `update` on `new_hires`, `run_tasks` and
`documents_meta`. Neither role names `pii_vault`, `pii_vault_aadhaar`, `org_keys`, `pii_access_log`
or `users`, so MongoDB refuses them even when the policy layer is bypassed. The server reads through
the read-only user and writes through the read-write user, and it refuses to start over a user that
holds anything other than exactly its role.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| With the policy layer bypassed, a write using the read-only user is refused by MongoDB (test) | yes | `apps/mcp-mongo/tests_int/test_db_users_int.py`, against a testcontainers `mongo:8.0.34@sha256:b9924c…` (the compose image). The users are provisioned by `db_users.provision` (run twice, to show it is idempotent). Tests: `test_the_read_only_user_cannot_insert_with_the_policy_bypassed[run_tasks\|new_hires\|audit_log]` and `test_the_read_only_user_cannot_update_with_the_policy_bypassed`, which call `TenantRepository` directly as the read-only user with no policy involved. Also `test_an_insert_tool_on_the_read_only_store_is_refused_by_mongodb`, which runs the `insert_one` tool handler with every `policy.*` check monkeypatched to a no-op over the read-only store. Each one asserts `OperationFailure.code == 13` (Unauthorized), and a root read afterwards confirms nothing was written. Command `uv run --frozen pytest apps/mcp-mongo/tests_int/test_db_users_int.py -v` gives `59 passed` (`docs/evidence/MCP-3/box-tests.txt`). Mutation M1 (read-only role given insert and update) fails all of these. |
| Reading `pii_vault`, `pii_vault_aadhaar`, `org_keys`, `pii_access_log` or `users` with any MCP user is refused by MongoDB (test) | yes | Same file. Each test runs for both users × all 5 collections (10 cases), and each denied collection is seeded by root first. `test_no_mcp_user_can_find_in_a_denied_collection[*]` and `test_no_mcp_user_can_count_or_aggregate_a_denied_collection[*]` go through the repository. `test_a_find_tool_on_a_denied_collection_with_the_policy_bypassed_is_refused[*]` uses a forged context naming the collection, with every policy check off. `test_no_pipeline_stage_reaches_a_denied_collection_with_a_raw_driver[*]` uses a raw PyMongo client as each user, with no policy and no repository, and runs `$lookup`, `$graphLookup`, `$unionWith`, `$out` and `$merge` from `run_tasks` into a denied collection. `test_the_read_write_user_cannot_write_into_a_denied_collection` covers writes. All of them expect code 13. Mutations M2 and M3 (a role naming denied collections) fail them. |
| The role definitions (`db.getUser` output) are recorded in `docs/evidence/MCP-3/` | yes | `docs/evidence/MCP-3/db-getUser.txt` holds the literal mongosh output of `db.getUser(<user>, {showPrivileges: true})` and `db.getRole(<role>, {showPrivileges: true})` for both users and both roles, on database `onboarding`. It was produced by `uv run --frozen python docs/evidence/MCP-3/record_roles.py > docs/evidence/MCP-3/db-getUser.txt`, which provisions a throw-away container with generated passwords; nothing secret is printed. |

`make check`: `make check: all passed`, exit 0. mcp-mongo has `235 passed` (`docs/evidence/MCP-3/make-check.txt`).

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Env.** `MCP_MONGO_URI` is the **read-only** user (`mcp_mongo_ro`). The new required `MCP_MONGO_WRITE_URI` is the **read-write** user (`mcp_mongo_rw`), and the two must differ. Both use `authSource=admin`. | `tests/test_main.py::test_settings_load_from_the_environment_with_safe_defaults`, `test_a_missing_required_setting_fails_fast[MCP_MONGO_WRITE_URI]`, `test_the_read_and_write_uris_must_differ` |
| **Start-up check.** `main` calls `db_users.assert_least_privilege` for each URI before serving. It uses `connectionStatus` with `showPrivileges`. The URI must authenticate as exactly that user in `admin` and hold exactly its role's privileges: nothing wider and nothing missing. Otherwise `PrivilegeError` is raised and nothing is served. | `test_main_checks_both_users_before_serving_and_routes_writes`, `test_main_refuses_to_serve_over_a_wider_user`, `tests/test_db_users.py::test_the_connection_check_*`, int `test_the_start_up_check_refuses_the_read_only_user_granted_an_extra_role`, `…_refuses_root_and_the_other_user` |
| **Routing.** `build_server(store, key, *, write_store=…)`. Tools whose contract has `read_only=False` (`server.WRITE_TOOLS`, now `insert_one` and `update_one`) use `write_store`; all other tools use `store`. If `write_store` is left out, writes use `store`, and MongoDB refuses them when `store` is the read-only user. | `tests/test_store_routing.py` (each tool reaches only its own connection, and every contract has a routing case) |
| **Provisioning.** `python -m onb_mcp_mongo.db_users` reads env `MCP_MONGO_ADMIN_URI`, `MCP_MONGO_DATABASE` (default `onboarding`), `MCP_MONGO_READ_PASSWORD` and `MCP_MONGO_WRITE_PASSWORD`. Passwords must be at least 24 characters and must differ. It is idempotent: `updateRole` and `updateUser` reset the roles to exactly the spec. Roles and users live in `admin`, and the privileges name the app database. | `tests/test_db_users.py::test_provision_*`, `test_the_provisioning_command_*`, int fixture provisioning twice |
| **Collection bounds.** Readable: `new_hires onboarding_runs run_tasks approvals documents_meta templates template_versions knowledge_docs conversations messages`. Writable (find, insert, update): `new_hires run_tasks documents_meta`. No user can remove, create indexes, run admin actions, or reach `audit_log`, the queues, `events`, `mock_outbox` or the global collections. | `db-getUser.txt`; `test_no_mcp_user_can_delete[*]`, `test_the_read_write_user_cannot_write_outside_its_collections`, `test_the_read_only_user_cannot_read_outside_its_collections`, `test_the_read_write_user_cannot_decide_an_approval` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none) | | | `plan.py show MCP-3` lists no ledger rows for this goal | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit 0. `NX Successfully ran target test for 17 projects`; mcp-mongo `235 passed` (`docs/evidence/MCP-3/make-check.txt`) |
| Integration | `make test-int` | exit 0. `NX Successfully ran target test-int for 5 projects`; mcp-mongo `77 passed` (MCP-1's 18 plus 59 here). Stack `ob-mcp-3` on ports 19600/19601 (`docs/evidence/MCP-3/make-test-int.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` (start of make-check.txt) |

`make test-xt` (T-3), the eval gates and `make test-pii` (SEC-1) do not exist on main yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| MCP-1 | The server connects with whatever `MCP_MONGO_URI` names. Split read and write | yes. Done here with `MCP_MONGO_WRITE_URI` and `write_store` |
| MCP-1 | `OperationFailure` becomes the denial `query_rejected` with fixed text, and the error is never echoed | yes. A MongoDB refusal for privileges surfaces this way |
| MCP-1 | A testcontainers Mongo with the pinned image; wait for `ping` | yes (`tests_int/conftest.py` `mongo_uri` and `root_db` reused) |
| T-1 | `TenantRepository` needs only `find` / `insert` / `update` for what the tools do | yes. The positive controls run count, `$group` aggregate, sorted and projected find, and the insert/update tools as the MCP users |

## Mutation checks
`docs/evidence/MCP-3/mutation-checks.txt` (script `mutate.py`): `SUMMARY: 12 of 12 mutations caught, every file restored`.
- M1: the read-only role gets insert and update. `test_the_read_only_user_cannot_insert_with_the_policy_bypassed[run_tasks]`, `[new_hires]`, `…cannot_update…` and `test_an_insert_tool_on_the_read_only_store_is_refused_by_mongodb` FAILED.
- M2: the read-only role names pii_vault, org_keys and users. `test_no_mcp_user_can_find_in_a_denied_collection[mcp_mongo_ro-*]`, the count/aggregate and find-tool variants, and the start-up check tests FAILED (11).
- M3: the read-write role names pii_vault_aadhaar and pii_access_log. The `[mcp_mongo_rw-*]` cases FAILED.
- M4: the read-write role may remove. `test_no_mcp_user_can_delete[mcp_mongo_rw]` FAILED.
- M5 / M5u: `check_privileges` accepts anything. Int `test_the_start_up_check_refuses_the_read_only_user_granted_an_extra_role` FAILED, and unit `test_a_wider_read_only_privilege_is_refused[*]` and others FAILED (8).
- M6: write tools use the read-only store. `test_each_tool_reaches_only_its_own_connection[insert_one\|update_one]` FAILED.
- M7: main skips the read-only check. `test_main_checks_both_users_before_serving_and_routes_writes` FAILED.
- M8: main passes the read store as `write_store`. The same test FAILED.
- M9: approvals become writable. `test_the_read_write_user_cannot_decide_an_approval` FAILED.
- M10: the identity check ignores the auth database. `test_the_connection_check_refuses_any_other_identity[user-in-app-db]` FAILED.
- M11: a user missing part of its role passes. `test_the_connection_check_refuses_a_user_missing_part_of_its_role` FAILED.

RED before GREEN: `docs/evidence/MCP-3/red-step1.txt` (`db_users` not importable) and `red-step2.txt` (settings, main and routing tests failing). The box integration tests were written after `db_users` existed, so they never ran red on their own; M1–M5 show they fail when a guard breaks.

## Review findings left open
I ran a code review and a security review (general-purpose agents with reviewer briefs). Neither found a critical or high issue, and neither found a path to the denied collections. Fixed in d38aece:
- approvals are no longer writable (security M1)
- the identity check matches the user and `admin` (security L3, code L4)
- the start-up check also refuses a user that is missing part of its role (code M2)
- `WRITE_TOOLS` is derived from `CONTRACTS[*].read_only`, and every contract needs a routing case (code M1)
- added pipeline-stage tests with a raw driver (security L6)
- added positive controls for count, aggregate, sorted find and the write tools (code M3)
- the CLI reuses `DEFAULT_DATABASE` (code L5)

Left open:
- **medium** · `db_users.py` `_WRITABLE` · `update` on `new_hires` can reach the `pii.*` token fields at the DB level. MongoDB roles can't restrict fields, so "token fields are read-only for agents" (§7.5) is enforced only by the policy (MCP-2). A follow-up could add a `$jsonSchema` collection validator or drop `new_hires` write if no agent needs it. Not built here because the card scopes roles, and the PRD §7.3 manifest example gates `update_one:new_hires` behind approval.
- **low** · `main.py` · the privilege check runs once at start-up, on its own short-lived client. A role granted afterwards isn't noticed until a restart. A periodic re-check is possible.
- **low** · views · an administrator who created a view named like an allowlisted collection over a vault collection would expose it. The MCP users cannot create views. An admin-side start-up check (`listCollections` with `type: "view"`) would close this.
- **low** · `server.py` `build_server(write_store=None)` falls back to the one store. I kept it to avoid touching every existing test, which keeps the MCP-2 merge small. `main` always passes both, and the fallback fails closed when the one store is the read-only user.
- **low** · `settings.py` · "URIs differ" is a string comparison only, an early message. The real guard is the start-up check.
- **low** · `rw` keeps `find` on its own collections (it is a subset of `ro`'s). Harmless, and it keeps upsert and future find-and-modify working.

## Left undone, and why
- **Compose wiring.** mcp-mongo has no compose service yet, so nothing in `infra/` provisions the users. Whoever adds the service (HUB-1, or the compose goal) should run `python -m onb_mcp_mongo.db_users` once as an init step with the root URI, and add `MCP_MONGO_WRITE_URI`, `MCP_MONGO_READ_PASSWORD` and `MCP_MONGO_WRITE_PASSWORD` to `.env.example`. I left `.env.example` and the Makefile untouched, because the card doesn't require them and they are shared files.
- **`/goal`.** This environment cannot set it. The condition from `plan.py goal MCP-3` was printed in-session and worked toward.

## Found for other goals
- **HUB-1 / REG-1:** when manifests load, check their `collections.read` / `write` against `db_users.READ_ONLY.collections` / `READ_WRITE.collections`. A manifest naming anything else (`template_drafts`, `template_change_requests`, `documents`, writes to `approvals` or `templates`) would otherwise fail at run time as `query_rejected`. Extending the sets is a one-line change in `db_users.py`, followed by re-provisioning.
- **MCP-2:** the `pii.*` read-only rule has no DB-level backstop (see open finding). Keep it covered by the validator. If you add a write tool, set `read_only=False` on its contract and it is routed to the write user automatically; `test_every_tool_has_a_routing_case` will ask for a routing case.
- **Approvals:** decisions are written by the API service's own DB user, never through MCP. The API's DB user (not designed yet) should get its own role.

## Decisions forced (ADRs written)
None written. Recorded here:
1. Roles and users live in `admin`, and the privileges name the app database. That gives one auth source for every service.
2. `MCP_MONGO_URI` keeps its name and becomes the read-only user. The writer is a new variable. This is the smallest shared change.
3. The server refuses to start unless each connection holds exactly its role, so a root URI in config fails loudly instead of silently voiding the defence.

## Traps hit
- mypy: a Protocol standing in for PyMongo's overloaded `Database.command` must be `(*args: Any, **kwargs: Any)`.
- The worktree guard refuses compound shell commands that mention git or write files through heredocs. The `rtk` hook truncates `make` output written to a file, so use `rtk proxy make …` for evidence files.
- The first integration version of the privilege-check test was satisfied by the user-name check alone, so M5 survived. I added the "right user, extra role" case (`grantRolesToUser readWrite`).
