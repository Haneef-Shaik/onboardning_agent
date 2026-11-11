# Handoff — MCP-2 · Reject dangerous and cross-tenant queries        attempt 7 (final) · goal/mcp-2-query-validator · see `git log -1` (code at 05dfefa)

**Attempt 7, the last one (the verifier failed attempt 6 on box 4; the orchestrator narrowed the model).**
The attempt-6 survivors:
- **A:** `server.py` gained a second `FastMCP` builder, and `main.py` started it with `FastMCP.run` in a thread on port+1. The test patched only `uvicorn.run`, so this listener went unseen.
- **B:** `build_server` appended a `drop_collection` tool only when the host is non-default **and** the port is default, a combination the two sampled configurations never hit.

The orchestrator **narrowed** the box-4 model rather than extending it (see Decisions forced). Survivor A is in the narrowed model and is closed. Survivor B is out of model by decision and is flagged for SEC-2 under Review findings left open.

Fixed in 05dfefa (`test_tool_surface.py`, tests only):
1. `test_fastmcp_is_constructed_exactly_once_inside_build_server` resolves `FastMCP` through `server.py`'s imports (any alias) and requires exactly one construction, inside `build_server`. No other module may import `mcp.server` (rule from attempt 6).
2. `test_nothing_in_the_package_starts_a_server_or_listener_but_mains_uvicorn_run` scans every module, admin code included. It forbids:
   - calls to `.run`/`.serve`/`.startup`/`.start*`/`.submit`/`.to_thread`/`.run_in_executor`/`create_task`/`run_until_complete`/`start_server`/`create_server`/`bind`/`listen`/`run_*_async`;
   - bare `Thread`/`Timer`/`Process`/`Server`/`Config`/`*PoolExecutor`;
   - imports of `threading`/`asyncio`/`anyio`/`socket`/`socketserver`/`multiprocessing`/`concurrent`/`subprocess`/`selectors`/`http.server` and other server stacks;
   - any `uvicorn` import except `import uvicorn` in `main.py`.

   The single allowed call is `main.py`'s one `uvicorn.run`, whose first argument must be `<the build_server result>.streamable_http_app()`.

Survivor A is mutation **V41**, killed by both new tests. The run also shows 2 errors, which come from the mutant's real second listener starting when the behavioural tests call `main()`. Total: **41 of 41** mutations caught.

**Attempt 6 (verifier failed attempt 5 on box 4, inside the threat model; the orchestrator approved this plan).**
The attempt-5 survivors were configuration-dependent branches. An aliased `FastMCP` with `Tool.from_function(db_users.run_command)` was served only when:
- **P1:** `settings.host != "127.0.0.1"`;
- **P2:** `settings.port != 8000`;
- **P3:** an ad-hoc env flag was set.

The production-path proof ran with default settings only, and the static layer matched local names only.

Fixed in d6948b5 (`test_tool_surface.py`, tests only):
1. **Every configuration.** The one-endpoint test and the `list_tools` test are parametrized over `required-only` and `all-non-default` (`MCP_MONGO_HOST=0.0.0.0`, `MCP_MONGO_PORT=9001`, `MCP_MONGO_DATABASE=onb_prod`). `test_every_optional_setting_is_exercised` checks that this covers every `MCP_*` variable `settings.py` reads.
2. **Static rule: no `mcp.server` outside `server.py`, under any name.** `test_only_the_server_module_imports_the_mcp_server_api` flags from-imports, plain imports, `from mcp import server` and any string naming `mcp.server` (for dynamic imports). The one allowed exception, listed explicitly, is `contracts.py` importing `ArgModelBase`, the SDK's argument-model base class, which can build neither a server nor a tool.
3. **Static rule: the environment is read only through `load_settings`.** `test_main_reads_the_environment_only_by_passing_it_to_load_settings` covers `main.py`: `os.environ`/`getenv`/`environ` (attribute, bare name, `getattr` string or `from os import`) and the `env` parameter may appear only inside the `load_settings(...)` argument. `test_no_other_module_reads_the_environment` allows only `main.py` and the admin `db_users.py`.

P1, P2 and P3 are mutations **V38–V40**, each killed:
- **V38 and V39:** killed by `list_tools[all-non-default]` and the `mcp.server` import rule.
- **V40:** killed by the `mcp.server` import rule and the environment rule.

Total: **40 of 40** mutations caught.

**Attempt 5 (verifier failed attempt 4 on box 4; the orchestrator confirmed the threat model below).**
The attempt-4 survivors:
- **M1:** a second MCP endpoint mounted at `/ops` on the served app.
- **M2:** an extra tool only when `main()` runs with env unset, which is the production call. The test always called `main(dict)`.

Fixed in 3a3f1e8 (`test_tool_surface.py`, tests only):
1. `_served_app` takes `main()`'s **production path**. Configuration comes from `monkeypatch.setenv` (`MCP_MONGO_URI`, `MCP_MONGO_WRITE_URI`, `MCP_CALL_CONTEXT_KEY`; optional `MCP_MONGO_*` removed), `main_module.main()` is called with no argument, and the exact app handed to `uvicorn.run` is captured.
2. `test_the_app_main_serves_is_exactly_one_mcp_endpoint` asserts:
   - the captured app is exactly a `Starlette` (not a wrapper or subclass);
   - its **whole route table**, walked recursively through every `Mount`, `Host` and `Router`, is exactly `[("Route", "/mcp", "StreamableHTTPASGIApp")]`;
   - `user_middleware` is empty.

   No health route is served today; one would have to be named in `EXPECTED_ROUTES`.
3. `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` (from attempt 4) now runs on that production-path app. It lists tools over a real MCP session on `/mcp` and requires exactly the six PRD tools.

M1 is mutation **V36**, killed by the route-table test. M2 is **V37**, killed by the behavioural `list_tools` test (and by the AST rule). Total: **37 of 37** mutations caught.

**Attempt 4 (verifier failed attempt 3 on box 4; orchestrator decision below).** The attempt-3
survivor was this: `main.py` built an aliased `FastMCP` (`FastMCP as Hub`) with
`tools=[..., Tool.from_function(db_users.run_command)]` and served that app. The old `main()` test
listed `build_server`'s tools, not the served app's, and the AST rule matched local names only.

Following the orchestrator's decision, box 4 is now proved **behaviourally**. The new test,
`test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp`:
1. Runs the real `main()` with only its I/O patched.
2. Captures the exact app object `main()` passes to `uvicorn.run`.
3. Starts that app's own lifespan.
4. Connects a real MCP `ClientSession` to it over streamable HTTP (in-process ASGI).
5. Calls `list_tools()` and asserts the set is exactly the six PRD tools, that no name or description
   matches delete/remove/drop/index/admin/run_command/command/eval/rename/replace (word match), and
   that each tool has `destructiveHint=False`.

The static source scan stays as a second layer, with the exact-path `db_users.py` exemption. The AST
rule stays as a cheap extra and is not relied on for aliases. The attempt-3 bypass is now mutation
**V35**, and only the behavioural test kills it:
`AssertionError: assert {'aggregate',...command', ...} == {'aggregate',... 'update_one'}` at
`test_tool_surface.py:213` (`docs/evidence/MCP-2/v35-behavioural-kill.txt`). Total: **35 of 35**
mutations caught.

**Attempt 3 (verifier failed attempt 2 on box 4 with two surviving mutations).**
- **Mb:** `main.py` registered a `run_command` tool whose body lived in the exempt `db_users.py`. The served-tool tests only inspected `build_server()`.
- **Md:** a nested `helpers/db_users.py` holding `.drop()` was exempt, because the exemption matched the stem at any depth.

Fixed in 90ec295, all in `test_tool_surface.py`:
1. (replaced in attempt 4 by the behavioural MCP-session test) `test_the_server_main_serves_has_exactly_the_prd_tools` ran the real `main()` with I/O patched and lists the tools of the server it hands to uvicorn. They must be exactly the PRD tools, with `destructiveHint=False`.
2. `test_only_the_server_module_builds_the_mcp_server_or_registers_tools` is an AST rule. Outside `server.py`, no file may call `add_tool`/`tool`/`remove_tool`/`resource`/`prompt` (including as decorators) or construct `FastMCP`/`Server`/`Tool`/`TracedTool`/`FuncMetadata`. It also asserts that `server.py` itself does, so the rule is not vacuous.
3. The exemption is now the exact resolved path `src/onb_mcp_mongo/db_users.py` (`ADMIN_FILES`). The scan covers every other `*.py` at any depth.
4. `test_the_package_has_no_subpackages` keeps the top-level import closure complete.

Mutations V32 (Mb), V33 (Mb as a `@server.tool()` decorator) and V34 (Md, a new nested file) are each caught: 34 of 34 in all.

**Attempt 2 (after the orchestrator rebased onto main, which now has MCP-3 and EVAL-1).** On the
rebased branch, `make check` failed on one test. The box-4 source scan flagged MCP-3's
`db_users.py`, which calls `.command()` for role provisioning and the start-up privilege check.
That module is admin code, not a tool. The scan now covers every module **except** the named admin
module (`ADMIN_MODULES = {"db_users"}`). New tests prove that no tool reaches it, both statically
(the AST import closure from `server.py`) and at run time (a fresh interpreter that imports every
tool module never loads it). Four new mutations (V28–V31) show that the narrowed guard still bites.
The property test now pins two look-alike-path examples, so it catches V15 every time. Commit
hashes below are the rebased ones.

**Outcome claimed.** Every mcp-mongo call now passes a recursive validator first
(`apps/mcp-mongo/src/onb_mcp_mongo/validator.py` with `validator_rules.py`). It runs once in
`server.TracedTool._run_traced`, after the contract and before policy and the driver, and walks each
argument in its own context:
- **Query documents:** filters, `$match`, `$pull` conditions.
- **Field conditions:** operators on a field's value.
- **Expressions:** `$expr`, `$project`, `$addFields`, `$group`.
- **Update operators:** at the top of an update.
- **Literals:** inserted documents and `$set` values.

It rejects:
- The PRD's forbidden operators, anywhere, as `forbidden_operator`.
- Any operator off its context's allowlist.
- Non-canonical (NFKC) or invisible field names.
- Writes to `pii.*` or `_id`.
- Any naming of `org_id`, as a field or as a `"$org_id"` path.
- `$$` system variables outside ROOT, CURRENT, REMOVE and NOW.
- Calls past the caps: depth, node count, 64-bit ints, string length, and `$map`/`$filter` nesting.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| An injection corpus of 40+ payloads (nested, dotted, unicode, `$where`, `$function`, `$accumulator`, `$expr` with JS, `$lookup`, `$graphLookup`, `$out`, `$merge`, `$unionWith`) is all rejected, each with a named reason (test) | yes | **Corpus:** `apps/mcp-mongo/tests/corpus.py` has **82 payloads**, each with an expected `denial.reason`. `REQUIRED_KINDS` maps every kind the card names to corpus ids. **Validator level:** `test_validator.py::test_every_corpus_payload_is_rejected_with_its_named_reason[*]` passes 82 of 82. **Supporting tests:** `test_the_corpus_has_at_least_40_payloads_with_unique_ids` and `test_the_corpus_covers_every_kind_the_card_names[*]` (12 kinds). **Over MCP with a real client:** `test_server_validator.py::test_every_corpus_payload_is_denied_over_mcp_before_the_driver[*]` passes 82 of 82. Each call is `isError`, the driver gets no calls, and the span has `outcome=denied` and `denial.reason` equal to the expected reason. Command: `uv run --frozen pytest apps/mcp-mongo/tests/test_validator.py apps/mcp-mongo/tests/test_server_validator.py apps/mcp-mongo/tests/test_validator_properties.py apps/mcp-mongo/tests/test_tool_surface.py -v` → `268 passed` (`docs/evidence/MCP-2/box-tests.txt`; it lists 82 PASSED for each corpus test). |
| A hypothesis test never produces an accepted query containing a forbidden operator | yes | `apps/mcp-mongo/tests/test_validator_properties.py`, 9 properties at 600 examples each. **Random trees:** `test_an_accepted_{filter,pipeline,update,insert}_never_holds_a_forbidden_operator`. Keys mix allowlisted operators, forbidden ones, look-alikes (fullwidth `＄`, Cyrillic `е`, zero-width, soft hyphen, case, padding) and random `$x`. **Grammar-built calls:** `test_a_mutated_{filter,pipeline,update,insert}_*`, where 1 key in 20 is replaced by a dangerous one; acceptance is 76% for find, 41% aggregate, 14% update and 81% insert (statistics in `box-tests.txt`), so the property is not vacuous. **Injection:** `test_a_valid_filter_is_accepted_and_rejected_once_a_forbidden_operator_is_injected` puts each forbidden operator at a random depth and checks both find and `$match`. **Oracle:** every name and dotted segment of an accepted call is checked, after NFKC, against the forbidden set and the allowlists. Per context, filters hold only query and expression operators, inserts no operator, and updates only update operators; `pii` and `org_id` are never written. Result: `600 passing, 0 failing` for each property (`box-tests.txt`). The property tests alone catch 8 of 10 operator and name mutations (`mutation-checks-properties.txt`); the 2 misses only change the reason, see Mutation checks. |
| An update touching `pii.mobile` and a `$set` of `org_id` are rejected (tests) | yes | **Validator:** `test_validator.py::test_an_update_touching_pii_mobile_is_rejected` (`pii_read_only`); `test_every_update_operator_on_pii_mobile_is_rejected[$set,$unset,$inc,$mul,$min,$max,$push,$addToSet,$pull]`; `test_a_set_of_org_id_is_rejected` and `test_a_set_of_an_org_id_subfield_is_rejected` (`org_id_not_allowed`). **Over MCP:** `test_server_validator.py::test_an_update_touching_pii_mobile_is_denied_over_mcp` (the stored `pii.mobile` is unchanged, the driver gets no calls, `denial.reason=pii_read_only`) and `test_a_set_of_org_id_is_denied_over_mcp` (`org_id` stays `org_a`). **Corpus:** `pii-*` (8 payloads) and `org-*` (10). All PASSED (`box-tests.txt`). |
| A test asserts no delete, drop, index, admin or `runCommand` tool exists | yes | `apps/mcp-mongo/tests/test_tool_surface.py` has ten tests. **Served tools:** `test_no_delete_drop_index_admin_or_run_command_tool_is_served` checks that `tools/list` over MCP is exactly the six PRD tools, that no name contains delete/remove/drop/index/admin/command/runcommand/eval/rename/replace, and that each tool has `destructiveHint=False`. **Built tools:** `test_the_server_is_built_only_from_the_prd_tools` checks `CONTRACTS` and `HANDLERS`. **What production serves (the primary proof; attempt 5 adds the production path and the route table):** `test_the_app_main_serves_is_exactly_one_mcp_endpoint` requires the app `main()` hands to uvicorn, called bare with env-var config, to be one MCP route at `/mcp` and nothing else. Then `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` captures the exact app `main()` hands to `uvicorn.run`, connects a real MCP client session to it, and asserts `list_tools()` is exactly the six PRD tools, with no forbidden word in any name or description and `destructiveHint=False`. **Who may register:** `test_only_the_server_module_builds_the_mcp_server_or_registers_tools` is the AST rule. **Source scan:** `test_no_source_file_but_the_admin_file_calls_a_delete_drop_index_or_command_method` scans every `*.py` under `src/`, at any depth, except the exact file `src/onb_mcp_mongo/db_users.py` for `.delete_*`, `.drop*`, `.create_index*`, `.command(`, `replace_one`, `bulk_write` and others, and asserts the scanned set covers the whole tool closure. **Admin code unreachable:** `test_the_tool_closure_holds_the_tool_code_and_not_the_admin_code` builds the AST import closure from `server.py`; it must contain every handler's and contract's module (plus tools, policy, validator and validator_rules) and must not contain `db_users`. `test_importing_the_tools_never_loads_the_admin_code` uses a fresh interpreter that imports every closure module and checks `db_users` is not in `sys.modules`. **The exemption itself:** `test_the_admin_exemption_is_exactly_the_provisioning_file` pins it to that one resolved path, and `test_the_package_has_no_subpackages` keeps the import closure complete. **Missing tools:** `test_calling_a_tool_that_does_not_exist_is_an_error` tries `delete_one`, `drop_collection`, `create_index`, `run_command` and `admin_command`. All 10 PASSED (`box-tests.txt`). Mutations V26–V41 fail them; V35, the attempt-3 bypass, is killed only by the behavioural test. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `onb_mcp_mongo.validator.check_call(tool, args)` raises `ToolRejectedError(reason)` on any disallowed argument and never changes `args`. The server calls it on every tool call, before policy, `TenantRepository` and the driver. | `test_the_validator_does_not_change_its_input` (whole corpus); the corpus-over-MCP test checks `db.calls() == []`; mutation V25 |
| New `denial.reason` values on spans: `forbidden_operator`, `operator_not_allowed`, `invalid_query`, `invalid_field_name`, `non_canonical_name`, `variable_not_allowed`, `pii_read_only`, `id_not_allowed`, `too_deep`, `too_large`, `unknown_tool`. `stage_not_allowed`, `invalid_stage`, `invalid_update` and `org_id_not_allowed` keep MCP-1's meaning. The message names the argument and, for forbidden ones, the operator; it never echoes agent text. | `validator_rules.REASONS`; `test_every_rejection_names_its_argument_and_never_echoes_agent_text` |
| **Allowed:** query operators `$eq $ne $gt $gte $lt $lte $in $nin $not $exists $type $all $size $elemMatch $mod $and $or $nor $expr`; update operators `$set $unset $inc $mul $min $max $push $addToSet $pull $currentDate $rename` (`$push`/`$addToSet` take `$each`, `$slice`, `$sort` and `$position`); and the expression and accumulator lists in `validator_rules.py`. **Not allowed:** `$regex`, `$text` and `$regexMatch` (ReDoS). The positionals `$` and `$[]` are allowed in update paths, but not as the first segment. | `test_ordinary_queries_are_accepted[*]`, `test_common_read_and_write_shapes_are_accepted[*]`, `test_context_rules_and_caps[*]` |
| `pii.*` is never written by insert or update, whatever the operator. Reads of `pii.*` (filters, projections, expressions) are allowed, since the fields hold tokens. | box 3 tests; `find-pii-read` accepted |
| Caps: `MAX_DEPTH` 32, `MAX_NODES` 10 000, ints within signed 64-bit, strings ≤ 65 536, `$map`/`$filter` nested ≤ 2. Very deep raw JSON (300+ levels) is refused by the MCP SDK as JSON-RPC -32602 before any tool runs; it is never an internal error. | `test_a_very_deep_filter_sent_as_raw_json_is_a_denial_not_an_internal_error[40,300]` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none) | | | `plan.py show MCP-2` lists no ledger rows | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `rtk proxy make check` (05dfefa) | `make check: all passed`, exit=0. Lint, typecheck and test for 17 projects; mcp-mongo `518 passed` (`docs/evidence/MCP-2/make-check.txt`) |
| Integration | `rtk proxy make test-int` (rebased branch) | exit=0. `12`, `10`, `3`, `28`, `77`, `17`, `8` and `2 passed`; `Successfully ran target test-int for 7 projects` (`docs/evidence/MCP-2/make-test-int.txt`, compose project `ob-mcp-2` on 19500/19501) |
| Box tests | `uv run --frozen pytest apps/mcp-mongo/tests/test_validator.py …/test_server_validator.py …/test_validator_properties.py …/test_tool_surface.py -v` | `283 passed`; 164 corpus PASSED lines (82 payloads × 2 tests) (`box-tests.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

`make test-xt`, evals and contracts-check do not cover this diff or do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| MCP-1 | Denials are MCP tool errors with `outcome=denied` and `denial.reason`; `ToolRejectedError` maps to them | yes. The validator raises `ToolRejectedError`, and the server tests read the reason from the span |
| MCP-1 | Arguments are validated by the closed contracts before the handler; `model_dump_one_level()` gives the handler kwargs | yes. The validator receives exactly those kwargs |
| MCP-3 (via the rebase) | `db_users.py` provisions roles and checks least privilege at start-up through `.command()`; `main.py` calls it, and the tools do not | yes. The AST closure from `server.py` and the runtime import check both show `db_users` is outside tool code |
| MCP-1 | "MCP-2 should cap depth; narrow `_mentions_org`; include sort keys" | Depth is capped and sort and projection keys are checked. `_mentions_org` in `policy.py` was **not** narrowed: it is MCP-1's code, and MCP-3 also edits this app, so it is left for a merge-safe follow-up. It still refuses a value equal to `"org_id"` after the validator accepts it, which is safe |

## Mutation checks
**All guards:** `docs/evidence/MCP-2/mutation-checks.txt` (script `docs/evidence/MCP-2/mutate.py`) gives `SUMMARY: 41 of 41 mutations caught, every file restored` (byte-for-byte sha256), run on the rebased branch.

| # | Guard broken | Failed |
|---|--------------|--------|
| V1 | Forbidden operators no longer named | 44 failed: `test_every_corpus_payload_is_rejected_with_its_named_reason[where-top]` and others |
| V2 | `$where` on the allowlist and the denylist check removed | 48 failed |
| V3 | `$expr` not walked | 17 |
| V4 | Expression operators not checked | 19 |
| V5 | Literals may carry operators | 12 |
| V6 | Field conditions accept any operator | 19 |
| V7 | `$match` not walked | 7 |
| V8 | `$group` accumulators not checked | 3, including `[accumulator-in-group]` |
| V9 | Forbidden stage names not named | 16 |
| V10 | Update operators not checked | 5 |
| V11 | `$pull` not walked | 2 |
| V12 | `pii.*` writable | 30, including `test_an_update_touching_pii_mobile_is_rejected` |
| V13 | `org_id` allowed in the validator | 22 |
| V14 | `$`-led segments allowed | 8 |
| V15 | NFKC check removed | 8 |
| V16 | Invisible characters allowed | 6 |
| V17 | Depth cap removed | 4 |
| V18 | Node cap removed | 3 |
| V19 | 64-bit int cap removed | 4 |
| V20 | `$$` variable allowlist removed | 3 |
| V21 | `"$path"` strings not checked | 4 |
| V22 | `$map` nesting cap removed | 3 |
| V23 | Positional first segment allowed | 3 |
| V24 | `_id` writable | 3 |
| V25 | Validator not wired into the server | 71 |
| V26 | A `delete_one` tool added | 5, the `test_tool_surface.py` tests |
| V27 | A `.drop(` call added to `tools.py` | 5, including `test_no_source_file_but_the_admin_file_calls_a_delete_drop_index_or_command_method` |
| V28 | A `.command(` call added to tool code (`tools.py`) | 5, including `test_no_source_file_but_the_admin_file_calls_a_delete_drop_index_or_command_method` |
| V29 | `tools.py` imports `db_users` | 3: `test_the_tool_closure_holds_the_tool_code_and_not_the_admin_code`, `test_importing_the_tools_never_loads_the_admin_code`, and the scan's closure-covered check |
| V30 | `validator_rules.py`, deep in the closure, loads `db_users` with `importlib` (invisible to the AST) | 1: `test_importing_the_tools_never_loads_the_admin_code` |
| V31 | A `.drop(` call added to `settings.py`, outside the tool closure but not admin code | 1: the source scan |

| V32 | Mb: `run_command` defined in `db_users.py` and registered by `main.py` with `server.add_tool` | 2: `test_only_the_server_module_builds_the_mcp_server_or_registers_tools`, `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` |
| V33 | Mb variant: `main.py` registers a tool with `@server.tool()` | the same 2 tests |
| V34 | Md: a new nested `helpers/db_users.py` with `.drop()`, imported by `tools.py` | 2: the source scan and `test_the_package_has_no_subpackages` |
| V35 | Attempt-3 bypass: `main.py` serves an aliased `FastMCP` holding the PRD tools plus `Tool.from_function(db_users.run_command)` | 1: `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` (`v35-behavioural-kill.txt`). The AST rule misses it, as the decision accepts |
| V36 | M1: a second MCP endpoint (aliased FastMCP, chained lifespan) mounted at `/ops`, serving `db_users.run_command` | 1: `test_the_app_main_serves_is_exactly_one_mcp_endpoint` |
| V37 | M2: `run_command` added only when `main()` runs with env unset (the production call) | 2: `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp` and `test_only_the_server_module_builds_the_mcp_server_or_registers_tools` |
| V38 | P1: aliased FastMCP + `Tool.from_function(db_users.run_command)` served only when the host is not the default | `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp[all-non-default]`, `test_only_the_server_module_imports_the_mcp_server_api` |
| V39 | P2: the same, served only when the port is not the default | the same 2 tests |
| V40 | P3: the same, served only when an ad-hoc env flag is set | `test_only_the_server_module_imports_the_mcp_server_api`, `test_main_reads_the_environment_only_by_passing_it_to_load_settings` |
| V41 | Survivor A: a second `FastMCP` (`run_command` over `db_users._auth_info`) built in `server.py` and started by `main.py` with `FastMCP.run` in a thread on port+1 | `test_fastmcp_is_constructed_exactly_once_inside_build_server`, `test_nothing_in_the_package_starts_a_server_or_listener_but_mains_uvicorn_run` |

V28–V41 test the narrowing:
- A destructive call in any file other than the one admin file fails.
- The admin module cannot become reachable from a tool, statically or dynamically.
- Nothing outside `server.py` can register a tool.
- What `main()` actually serves, on its production path, in every configuration, is one MCP endpoint, and that endpoint lists exactly the PRD tools over a real MCP session.
- No module but `server.py` touches the MCP server API, and no code path depends on environment variables that `settings.py` does not define.

V32 and V34 are the verifier's attempt-2 survivors. Both are caught now, and the temporary files V34 creates are removed afterwards.

**Property tests alone (box 2):** `mutation-checks-properties.txt` reruns V1, V2, V4, V5, V6 and V12–V16 against `test_validator_properties.py` only and catches 8 of 10. In attempt 2, the update property pins `{"$set": {"＄where": 1}}` and `{"$set": {"a.﹩function": 1}}` as `@example`s. Before that, V15 (NFKC) was caught only when hypothesis happened to put a look-alike in a plain path position. The two it does not catch are expected:
- **V1** only changes the denial reason; the operator is still rejected.
- **V16** with a `$`-led look-alike is still rejected as not allowlisted, and an invisible character in a plain field name is not dangerous.

The corpus tests catch both.

**RED before GREEN:**
- `docs/evidence/MCP-2/red-step1.txt`: `ModuleNotFoundError: No module named 'onb_mcp_mongo.validator'`.
- `red-step1-server.txt`: 59 failed through the server before the validator existed.
- The tool-surface tests passed from the start: MCP-1 already serves no destructive tool, so they are a guard against regression (V26 and V27 show they bite).

## Review findings left open
- **flag for SEC-2 · out of the box-4 model by decision · survivor B (verifier, attempt 6), quoted:** "build_server gains `if host != '127.0.0.1' and port == 8000: tool_list.append(Tool.from_function(_noop, name='drop_collection'))` (a networked host on the default port, arguably the most realistic deployment) → 516 passed. Why missed: the proof samples only two configurations (required-only = default host; all-non-default = port 9001), so any branch on a mixed default/non-default combination is never exercised; the code sits in server.py so neither static rule applies." Box 4 does not guard against this kind of change; code review and SEC-2 must. Suggested SEC-2 check: `build_server` and `main` use host, port and database only as pass-through arguments.
I ran a code review and a security review (general-purpose agents with reviewer briefs). Neither found a critical or high issue, and the security review found **no bypass** (no JavaScript, no cross-collection or cross-org access, no `pii`/`org_id` write). These were fixed in 839bf45 (rebased hash):
- Query and condition contexts are now separate.
- ObjectId is accepted only in value position.
- `$$` system-variable allowlist; `"$org_id"` paths refused.
- Caps on ints, strings and `$map` nesting.
- `$sort` keys counted toward the node cap.
- Dotted output names refused.
- Positional first segment and `_id` writes refused.
- `$push` modifiers allowed.
- More expression operators allowed.
- Per-context property oracles.
- The near-tautological tests replaced.
- Immutability checked over the whole corpus.

Left open:
- **medium** · `libs/py-data` (repository) · there is no `maxTimeMS` on find, count or aggregate. An allowlisted but expensive query (`$group` or `$sort` over a large org, or a 2-level `$map` over a large array) can hold mongod CPU. The validator caps the query's shape; a time limit belongs to the data layer. This is the same finding MCP-1 left open. **For a py-data follow-up, or HUB-1/OW.**
- **medium** · no date literal. `{"$date": ...}` is not decoded: `policy.decode_ids` handles only `$oid`, and changing it is MCP-1's code and would clash with MCP-3. Agents can compare ISO strings, or use `$expr` with `$toDate`. **Follow-up:** decode `{"$date": ISO}` in `decode_ids` and allow it in the validator's ObjectId-style leaf check.
- **low** · total request size is not capped in bytes; only nodes and per-string length are. A body limit belongs at the hub or ingress.
- **low** · `policy.py` still repeats the `org_id`, stage and update-shape checks after the validator (defence in depth). For shared cases the reasons match, apart from the `$set: 5` case below.
- **low** · `contracts.py` tool descriptions don't mention that `$regex` is off. It is left unchanged to keep the shared file out of this diff; the denial says "operator not allowed there".

## Left undone, and why
- **Narrowing `policy._mentions_org`** (MCP-1's low finding). See Inherited and used. It is safe as it is.
- **`/goal`.** This environment cannot set it. The condition from `plan.py goal MCP-2` was printed in-session, and each box is proved above.

## Changed outside the new module (kept minimal for the MCP-3 merge)
- `server.py`: one import and one call, `validator.check_call(self.name, args)`. `args` is now the `model_dump_one_level()` dict.
- `tests/test_server.py::test_a_malformed_update_operator_is_denied_not_an_internal_error`: the expected reason changes from `invalid_query` to `invalid_update`, because the validator now refuses `{"$set": 5}` before the data layer. The assertion is equally strict: still a denial, still a named reason.
- `tests_int/test_mcp_mongo_int.py::test_a_query_mongodb_refuses_is_a_denial_not_an_internal_error`: the input changes from `{"$bogus": 1}`, which the validator now refuses, to `{"$mod": [0, 0]}`, which passes the validator and is refused by MongoDB. The assertions are unchanged, so the `OperationFailure → query_rejected` path stays covered.

## Found for other goals
- **MCP-3 (merged):** `db_users.py` is the only module allowed to call `.command()` and similar admin methods, and only while no tool reaches it. A future admin module must be added to `ADMIN_FILES` (and `ADMIN_MODULES`) in `test_tool_surface.py`, which `test_the_admin_exemption_is_exactly_the_provisioning_file` pins, so that is a deliberate, reviewed change. If tool code needs anything from `db_users` (such as the role constants), move it to a module that has no admin calls.
- **HUB-1:** the `denial.reason` values above can feed the audit record. Consider a request-body byte cap.
- **py-data / ops:** `maxTimeMS` on every repository call (see open findings).
- **MCP-1 follow-up:** `$date` decoding; narrow `_mentions_org`.

## Decisions forced (ADRs written)
**FINAL box-4 model (attempt 7).** The orchestrator decided it as integrator, on Haneef's behalf; he can override it. Box 4 was not converging, so the model is **narrowed, not extended**, and attempt 7 is the last attempt.

The tests guarantee:
1. The app `main()` serves through the production path exposes exactly one MCP endpoint, and its tools are exactly the six PRD tools, under the required-only and all-non-default configurations. Tests: `test_the_app_main_serves_is_exactly_one_mcp_endpoint[*]` and `test_the_app_main_serves_lists_exactly_the_prd_tools_over_mcp[*]`.
2. No module except `server.py` imports from `mcp.server` (any alias), and `FastMCP` is constructed exactly once, inside `build_server`. Tests: `test_only_the_server_module_imports_the_mcp_server_api` and `test_fastmcp_is_constructed_exactly_once_inside_build_server`.
3. Nothing in the package starts a server or listener except `main()` handing that one app to `uvicorn.run`. Test: `test_nothing_in_the_package_starts_a_server_or_listener_but_mains_uvicorn_run`.

**Out of scope, by decision:**
- code deliberately written to branch on configuration combinations so that it evades the sampled configurations (survivor B);
- code written to defeat these exact static rules.

That is the job of code review and SEC-2. Not added, by decision: the 8-configuration product, and a host/port pass-through rule.

Kept as further layers: the environment-only-via-`load_settings` rule, and the source scan with the exact-path `db_users.py` exemption.

### Superseded: threat model for attempts 5–6
**Box 4 threat model (attempt 5).** The orchestrator confirmed it as integrator, on Haneef's behalf; he can override it.

Box 4 guards against a forbidden (delete/drop/index/admin/runCommand) tool being served by any **ordinary route**:
- registering a tool;
- aliasing the server class;
- mounting a second endpoint;
- configuration-dependent code paths.

It does **not** claim to defeat a hostile rewrite of the test or of `main()` written specifically to dodge the test. Review and the static source scan cover that.

The proof:
1. Call `main()` through the production path (env vars via `monkeypatch.setenv`, no argument).
2. Capture the app handed to `uvicorn.run`.
3. Assert its whole route table is exactly one MCP endpoint at `/mcp`. A health route, if one is ever served, must be named explicitly.
4. List tools over a real MCP session on `/mcp`; they must be exactly the six PRD tools.
5. **Attempt 6 (approved):** steps 1–4 run in every configuration (required-only and all non-default). Two static rules back them: `mcp.server` may be imported only by `server.py`, under any name; the environment may be read only by passing it to `load_settings`.

The static scan, with the exact-path `db_users.py` exemption, stays as the second layer.

### Earlier decision (attempt 4)
**Box 4 proof, attempt 4.** The orchestrator decided this as integrator, on Haneef's behalf; Haneef can override it.
- **Option rejected:** moving `db_users.py` out of the app. The start-up privilege check must stay in the served app and needs `.command("connectionStatus")`, and moving the file would rewrite MCP-3's merged code.
- **Decision:** box 4 is proved behaviourally, as its text literally asks ("a test asserts no … tool exists"). A real MCP client lists the tools of the exact app `main()` serves. This catches every registration or aliasing bypass, whatever the source looks like.
- **Second layer:** the static source scan, with the exact-path `db_users.py` exemption.
- **Not done:** the AST rule is not extended to resolve aliases.

No ADR file was written; this record and the orchestrator's message are the decision.

Other choices made:
1. `$regex`, `$text` and `$regexMatch` are off for ReDoS reasons.
2. A field-path string in an expression is checked like a field, so `"$org_id"` is refused even though it only reads the caller's org. This keeps "org_id is never named" literal.
3. The validator runs in the server, not in each handler, so every tool is covered by one call.

## Traps hit
- Hypothesis random trees almost never pass a strict validator (about 1%), so the property was nearly vacuous. Grammar-built calls with 1-in-20 mutated keys fixed it.
- The MCP SDK client cannot serialise very deep JSON (pydantic depth limit), so deep payloads are sent as raw JSON-RPC.
- The rtk hook filters `make` output. Use `rtk proxy make …` to capture full gate logs.
- Raw confusable characters in test files trip ruff RUF001. Write them as `\u` escapes.
