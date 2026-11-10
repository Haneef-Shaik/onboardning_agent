# Handoff — MCP-1 · Serve MongoDB tools over MCP with strict contracts        attempt 1 · goal/mcp-1-mongo-mcp-server · see `git log -1` (code at 8bd61c6)

**Outcome claimed.** `apps/mcp-mongo` is a FastMCP server (the official `mcp` SDK 1.30,
`mcp.server.fastmcp`) on streamable HTTP (`/mcp`, stateless, JSON responses). It serves
`list_collections`, `find`, `count`, `aggregate`, `insert_one` and `update_one`. Each tool has a
closed JSON-schema contract (`contracts.py`). Tenant scope comes only from a hub-signed call
context: an HS256 JWS in the `X-Onb-Call-Context` header that names one tool. All database access
goes through `onb_data.TenantRepository`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| An MCP client lists and calls every tool in an integration test against testcontainers Mongo | yes | `apps/mcp-mongo/tests_int/test_mcp_mongo_int.py`. A real `mcp.ClientSession` uses `streamable_http_client` to reach uvicorn on loopback. The server is backed by a `testcontainers.community.mongodb.MongoDbContainer` running `mongo:8.0.34@sha256:b9924c…` (the compose image, pinned), with a per-session root password (`tests_int/conftest.py`). `test_an_mcp_client_lists_every_tool_with_its_contract` checks that the listed tools are the six PRD tools, each with `additionalProperties: false` and an output schema. These tests call one tool each against real data: `test_list_collections`, `test_find_sees_only_the_callers_org`, `test_find_by_object_id_round_trips`, `test_count`, `test_aggregate_groups_within_the_callers_org`, `test_insert_one_takes_org_id_from_the_context`, `test_update_one_cannot_reach_another_org` and `test_an_id_from_find_updates_the_document_as_oid`. Command `uv run --frozen pytest apps/mcp-mongo/tests_int -v` gives `18 passed` (`docs/evidence/MCP-1/box-tests.txt`). Through the gate: `make test-int` shows `18 passed` and `NX Successfully ran target test-int for 4 projects`, exit=0 (`docs/evidence/MCP-1/make-test-int.txt`). |
| `find` without `limit` or with `limit` > 100 is rejected (test) | yes | Over MCP and the real server: `tests/test_server.py::test_find_without_limit_or_over_100_is_rejected[args0-3]` covers no limit, 101, 0 and 1000. The error names `limit` and the driver is never called. Against Mongo: `tests_int/…::test_find_without_limit_or_over_100_is_rejected[no-limit]` and `[limit-101]`. Contract: `tests/test_contracts.py::test_find_requires_a_limit_of_at_most_100` checks the schema has `required` containing limit, `maximum` 100 and `minimum` 1. `test_find_without_a_valid_limit_is_rejected[*]` adds `"10"`, `10.5` and `true`, which strict ints reject. Mutations M1, M2 and M16 fail these (below). |
| An agent-supplied `org_id` in a filter is rejected; inserts get `org_id` from the context (tests) | yes | **Rejected:** `tests/test_server.py::test_an_agent_supplied_org_id_is_rejected[*]` (8 cases) covers find with `org_b`, with its own `org_a`, and with a nested `$or`; count with `$ne`; an aggregate `$match`; an update filter; an update `$set`; and an insert document. The driver is never called. `test_org_id_as_a_tool_argument_is_rejected[*]` passes `org_id` as a top-level argument to each of the six tools. The error is `org_id (extra_forbidden)`, and the control test `test_the_same_args_without_org_id_succeed` passes. `tests/test_policy.py::test_org_id_anywhere_is_rejected[*]` (11 cases, including dotted paths and `$rename` targets). Against Mongo: `tests_int/…::test_an_agent_supplied_org_id_is_rejected[*]` (5 tools); the data is unchanged afterwards. **Inserts:** `tests/test_server.py::test_insert_gets_org_id_from_the_context` and `tests_int/…::test_insert_one_takes_org_id_from_the_context`, which reads the stored document back with a root client and finds `org_id == "org_a"`. `test_the_org_comes_from_the_context_of_each_call` and `test_concurrent_calls_keep_their_own_orgs` run 4 concurrent sessions alternating org_a and org_b. Mutations M3, M4, M5 and M15 fail these. |
| Each call's span carries `db.operation`, `db.collection`, `agent.name`, `org_id` and a result count, and no document content (in-memory exporter test) | yes | `tests/test_spans.py` uses `onb_telemetry.testing.telemetry`, the in-memory exporter. `test_each_call_has_one_span_with_the_required_attributes[<each of the 6 tools>]` checks there is exactly one `mcp.mongo.<tool>` span. It must have `db.operation`, `db.collection` (`*` for list_collections), `agent.name`, `org_id`, `run_id`, `db.result_count` with the expected value, and `outcome=ok`. `test_no_span_carries_document_content[*]` seeds a marker string in documents, inserts and updates, and asserts it is absent from every span's attributes, events and status (filter values too). Further tests: denied spans carry a reason and no foreign org (`test_a_rejected_call_has_a_denied_span_with_a_reason`); a missing context gives no `org_id` attribute; an off-pattern collection name is not put on the span; an error span has `error.type` but not the message. End to end: `tests_int/…::test_every_call_span_has_the_attributes_and_no_document_content` runs with PyMongo instrumentation on and finds no document content in any span, the driver's included. Mutations M10–M13 fail these. |

`make check`: `make check: all passed`, exit=0 (`docs/evidence/MCP-1/make-check.txt`, mcp-mongo unit tests `191 passed`).

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Call-context contract (for HUB-1).** HS256 JWS, `iss=mcp-hub`, `aud=mcp-mongo`. The claims `iat`, `exp` (lifetime ≤ 60 s, 5 s leeway), `org_id`, `agent`, `run_id`, `tool` and `collections: {read: [...], write: [...]}` are all required. It goes in header `X-Onb-Call-Context`. Sign it with `onb_mcp_mongo.call_context.sign_call_context(CallContext(...), key)`. The key is env `MCP_CALL_CONTEXT_KEY`, ≥ 32 characters, and the hub and mcp-mongo share it. A context is valid only for the tool it names. | `tests/test_call_context.py` (all), `test_a_context_for_another_tool_is_refused` |
| Writable collections are readable too. `list_collections` returns read ∪ write from the context. A global collection (`orgs`, `agent_registry`, `tool_catalog`) is refused even when the context lists it. | `test_list_collections_*`, `test_a_global_collection_in_the_context_is_still_refused` |
| Denials come back as MCP tool errors (`isError: true`) with fixed text. The span has `outcome=denied` and `denial.reason` set to one of `no_call_context`, `context_tool_mismatch`, `invalid_arguments`, `org_id_not_allowed`, `collection_not_allowed`, `stage_not_allowed`, `invalid_stage`, `invalid_update`, `id_not_allowed`, `tenant_violation`, `invalid_query` or `query_rejected`. Unexpected failures give `outcome=error` with `error.type` only, and the text `<tool>: internal error`. Errors never echo submitted values. Unknown argument keys appear as `<extra>`, except `org_id`, which is named. | `test_spans.py`, `test_an_invalid_argument_error_names_the_field_but_not_the_value`, `test_a_malformed_update_operator_is_denied_not_an_internal_error`, `tests_int/…::test_a_query_mongodb_refuses_is_a_denial_not_an_internal_error` |
| Results: `find`/`aggregate` return `{documents, count}`, `count` returns `{count}`, `insert_one` returns `{inserted_id, count: 1}`, and `update_one` returns `{matched, modified, count}`. ObjectIds come out as hex strings and go in as `{"$oid": "<hex>"}`; no other extended-JSON form is decoded. `aggregate` appends `$limit: 100`. | `test_find_serialises_object_ids_and_accepts_them_back`, `test_an_id_from_find_updates_the_document_as_oid`, `test_aggregate_matches_the_org_first_and_caps_the_result` |
| `find` `projection` accepts only 0/1 values; `sort` takes `[field, 1\|-1]` pairs; `skip` ≤ 10 000. Insert documents may not carry `_id`. Updates must use operators. | `test_contracts.py`, `test_a_projection_is_only_zeros_and_ones`, `test_insert_with_an_agent_chosen_id_is_rejected` |
| Run it with `python -m onb_mcp_mongo.main`. Env: `MCP_MONGO_URI` and `MCP_CALL_CONTEXT_KEY` (both required, no defaults); `MCP_MONGO_DATABASE` (default `onboarding`), `MCP_MONGO_HOST` (default `127.0.0.1`), `MCP_MONGO_PORT` (default 8000). It calls `init_telemetry("mcp-mongo", instrument=True)`. | `tests/test_main.py` |
| A project's integration tests can start a throw-away Mongo with `testcontainers.community.mongodb.MongoDbContainer` and the pinned image. See `apps/mcp-mongo/tests_int/conftest.py`, which waits for `ping` because the image's init step starts a temporary mongod first. | `make-test-int.txt` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none) | | | `plan.py show MCP-1` lists no ledger rows for this goal | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. 17 projects; mcp-mongo `191 passed` (`docs/evidence/MCP-1/make-check.txt`) |
| Integration | `make test-int` | exit=0. Stack `12 passed`, py-data `24 passed`, py-telemetry `3 passed`, `2 passed`, mcp-mongo `18 passed`, `Successfully ran target test-int for 4 projects` (`make-test-int.txt`, stack `ob-mcp-1` on 19200/19201) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

Cross-tenant (`make test-xt`, T-3), evals and the PII-scan gate do not exist on main yet. The
cross-tenant behaviour of every tool is covered here by `test_find_sees_only_the_callers_org`,
`test_count`, `test_update_one_cannot_reach_another_org`, `test_find_by_object_id_round_trips` (a
foreign id finds nothing) and `test_concurrent_calls_keep_their_own_orgs`. **T-3/HUB-1 must add the
MCP tools to `make test-xt`.**

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 (via main) | `TenantRepository` adds `org_id` to filters and inserts and puts the `$match` first in pipelines; there is no raw collection access | yes. `test_find_returns_only_the_callers_org` and `test_aggregate_matches_the_org_first…` show the exact driver calls |
| F-2 | `make test-int` fans out to every project's `test-int` target; a per-worktree compose project works | yes (ob-mcp-1, 4 projects) |
| F-4 / OBS-1 | `onb_telemetry.testing.telemetry` / `captured_telemetry(instrument=True)` | yes. One trap: a module-level `trace.get_tracer()` stays bound to the first provider, so the tracer is fetched per call |
| F-1 | Workspace deps go in the member `pyproject.toml`, then `uv lock` | yes (`mcp`, `pyjwt`, `pydantic`, `uvicorn`, `onb-data`, `onb-telemetry`; dev `testcontainers`) |

## Mutation checks
`docs/evidence/MCP-1/mutation-checks.txt` (script `docs/evidence/MCP-1/mutate.py`): `SUMMARY: 20 of 20 mutations caught`. Every file was restored byte for byte.
- M1: the find limit max is 1000. `test_contracts.py::test_find_requires_a_limit_of_at_most_100`, `test_find_without_a_valid_limit_is_rejected[args1]` and the server tests FAILED (7 failed).
- M2: `limit` is optional with a default of 100. `test_find_without_a_valid_limit_is_rejected[args0]` and others FAILED (4).
- M3: `reject_org_id` is a no-op. `test_policy.py::test_org_id_anywhere_is_rejected[*]` and the server `org_id` tests FAILED (21).
- M4: `org_id` is checked at the top level only. The nested `$and`/`$or` cases FAILED (11).
- M5: insert takes `org_id` from the document. `test_an_agent_supplied_org_id_is_rejected[insert_one-args7]` FAILED.
- M6: contracts use `extra="ignore"`. `test_every_contract_is_a_closed_object_schema[*]` and `test_org_id_as_a_tool_argument_is_rejected[*]` FAILED (16).
- M7: the JWS signature is not verified. `test_a_context_signed_with_another_key_is_refused` and others FAILED (6).
- M8: writes are allowed on readable collections. `test_writable_collections_come_from_the_context` and `test_insert_into_a_read_only_collection_is_rejected` FAILED.
- M9: `$lookup` is added to the allowlist. `test_the_allowed_stages_are_exactly_the_prd_list` and `test_a_stage_off_the_allowlist_is_rejected[$lookup]` FAILED.
- M10: the span loses `org_id`. `test_spans.py::test_each_call_has_one_span_with_the_required_attributes[*]` FAILED (7).
- M11: the span loses the result count. The same tests FAILED (6).
- M12: the span carries `repr(arguments)`. `test_no_span_carries_document_content[*]` FAILED (5).
- M13: the error span status carries `str(exc)`. `test_an_unexpected_failure_has_an_error_span_without_its_message` FAILED.
- M14: validation errors use `str(exc)`. `test_an_invalid_argument_error_names_the_field_but_not_the_value` FAILED.
- M15 (int): `reject_org_id` is a no-op against testcontainers Mongo. `tests_int/…::test_an_agent_supplied_org_id_is_rejected[*]` FAILED (5).
- M16 (int): the limit max is 1000 against Mongo. `tests_int/…::test_find_without_limit_or_over_100_is_rejected[limit-101]` FAILED.
- M17: the tool binding of the context is off. `test_a_context_for_another_tool_is_refused` FAILED.
- M18: `reject_id` is off. `test_insert_with_an_agent_chosen_id_is_rejected` FAILED.
- M19: the projection accepts any int. `test_a_projection_is_only_zeros_and_ones[projection2/3]` FAILED.
- M20: malformed queries become internal errors. `test_a_malformed_update_operator_is_denied_not_an_internal_error` FAILED.

RED before GREEN: `docs/evidence/MCP-1/red-step1.txt` (call context, policy and contracts: module not found), `red-step2.txt` (server and spans: `onb_mcp_mongo.server` not found) and `red-review-fixes.txt` (review-fix tests failing to collect on the new `CallContext.tool`). The integration tests were written after the unit-tested server, so they were not seen red on their own. M15 and M16 show they fail when a guard breaks.

## Review findings left open
A code review and a security review (general-purpose agents with reviewer briefs) found no critical
or high issues and no cross-tenant path. These fixes were made in 8bd61c6:
- The context is bound to one tool (`tool` claim), with lifetime ≤ 60 s and 5 s leeway.
- Agent-chosen `_id` is rejected (cross-org existence oracle).
- Projections are 0/1 only (closes `$function` in projection).
- Arguments are validated directly, so FastMCP no longer JSON-pre-parses strings into objects.
- Malformed queries, `OperationFailure` and global collections are denials, not internal errors.
- Unknown argument keys are not echoed.
- Result conversion runs inside the span's error handling.
- Stronger `org_id` argument test, plus a concurrency test.

Left open:
- **medium** · `call_context.py` · a context can be replayed for the same tool within its 60 s life (no `jti`, no args digest). Binding a context to one call needs a `jti` cache and an argument digest, which is the hub's issuance design. **For HUB-1:** decide on `jti` + `args_sha256`; the verifier side is a small addition here.
- **medium** · `tools.py` · no `maxTimeMS` and no result byte cap. `count`, `$group` and `$sort` scan the whole org, and an aggregate can return up to 100 large documents. `TenantRepository` has no `max_time_ms` parameter, and that belongs to py-data. **For MCP-2 / a py-data follow-up.**
- **medium** · `server.py` · the server builds `FuncMetadata` and subclasses `ArgModelBase` from the SDK. Validation and the call are now done here; FastMCP's internals are used only for `convert_result` and the output schema. `mcp` is constrained `>=1.20,<2`; pin tighter if an SDK upgrade breaks it. `test_server.py` covers the integration.
- **low** · `server.py:build_server` · DNS-rebinding protection is off. The server is meant for the private network and every call needs a hub-signed context. When mcp-mongo joins compose, set `allowed_hosts` to the service name.
- **low** · `main.py` · the Mongo client is not closed on shutdown (no lifespan). The process exits anyway; add a lifespan when the service joins compose.
- **low** · `policy.py:_mentions_org` · it also rejects a *value* equal to `"org_id"` (to catch `$rename` targets), so `{"label": "org_id"}` is refused. This is safe. Narrow it in MCP-2's validator.
- **low** · recursion in `_mentions_org`, `decode_ids` and `to_json` has no depth cap. Very deep JSON gives `RecursionError`, which surfaces as an internal error, not a denial. MCP-2's validator should cap depth.
- **note** · `except (TypeError, ValueError)` around the handler maps data-layer argument errors to `invalid_query`, so a genuine bug raising one of these would be reported as a denial.

## Left undone, and why
- **Audit records per call** (PRD §7.5 "audit record + span"). This is not a Done-when box, and the audit log belongs to the hub/audit goals (HUB-1). The span is in place.
- **`requires_approval`.** The hub surfaces it (HUB-1 scope); mcp-mongo only receives already-approved calls.
- **The recursive operator validator** (`$where`, `$function`, `$expr`, `pii.*` read-only) is MCP-2. **MDB least-privilege users** are MCP-3.
- **A compose service for mcp-mongo.** Not on this card. The `MCP_*` variables will need `.env.example` entries when it is added.
- **`/goal`.** This environment cannot set it. The condition from `plan.py goal MCP-1` was printed in-session; every box was proved above.

## Found for other goals
- **MCP-2:** do the validator in one `policy.check_call(tool, ctx, args)` entry. Include `sort` keys, and depth caps. Narrow `_mentions_org`. Consider `maxTimeMS` together with a byte cap.
- **MCP-3:** the server connects with whatever `MCP_MONGO_URI` names. Split it into read-only and read-write URIs (or users) and route `insert_one`/`update_one` through the read-write one.
- **HUB-1:** sign contexts with `sign_call_context`, one per tool call, using the shared `MCP_CALL_CONTEXT_KEY`. Decide on replay protection (`jti` + args digest). Add the mcp-mongo tools to `make test-xt`.
- **T-3:** the cross-tenant suite should include the six mcp-mongo tools (see the int tests for a pattern).

## Decisions forced (ADRs written)
None written. These design choices are recorded here for HUB-1:
1. The call context is a separate HS256 JWS from the hub, not the agent token itself, so the agent token never reaches downstream servers.
2. A context names one tool.
3. The official `mcp` SDK's FastMCP was chosen, not the standalone `fastmcp` package.

## Traps hit
- A module-level OTel tracer stays bound to the first provider, so spans vanished in tests. The tracer is now fetched per call.
- FastMCP's generated argument models ignore unknown arguments and JSON-decode string values. The contracts use `extra="forbid"` and are validated directly.
- `Literal[1, -1]` in pydantic accepts `True` and `1.0`. Strict int plus a validator is used, served as `enum`.
- In mongo's docker image the temporary init mongod also logs "waiting for connections", so wait for `ping`.
- The session's worktree guard rejects compound shell commands that write files; use Edit/Write instead.
