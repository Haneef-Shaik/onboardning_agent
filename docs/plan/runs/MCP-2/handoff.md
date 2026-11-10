# Handoff — MCP-2 · Reject dangerous and cross-tenant queries        attempt 3 · goal/mcp-2-query-validator · see `git log -1` (code at 90ec295)

**Attempt 3 (verifier failed attempt 2 on box 4 with two surviving mutations).**
- **Mb:** `main.py` registered a `run_command` tool whose body lived in the exempt `db_users.py`. The served-tool tests only inspected `build_server()`.
- **Md:** a nested `helpers/db_users.py` holding `.drop()` was exempt, because the exemption matched the stem at any depth.

Fixed in 90ec295, all in `test_tool_surface.py`:
1. `test_the_server_main_serves_has_exactly_the_prd_tools` runs the real `main()` with I/O patched and lists the tools of the server it hands to uvicorn. They must be exactly the PRD tools, with `destructiveHint=False`.
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
| A test asserts no delete, drop, index, admin or `runCommand` tool exists | yes | `apps/mcp-mongo/tests/test_tool_surface.py` has ten tests. **Served tools:** `test_no_delete_drop_index_admin_or_run_command_tool_is_served` checks that `tools/list` over MCP is exactly the six PRD tools, that no name contains delete/remove/drop/index/admin/command/runcommand/eval/rename/replace, and that each tool has `destructiveHint=False`. **Built tools:** `test_the_server_is_built_only_from_the_prd_tools` checks `CONTRACTS` and `HANDLERS`. **What production serves:** `test_the_server_main_serves_has_exactly_the_prd_tools` runs `main()` and lists the served server's tools. **Who may register:** `test_only_the_server_module_builds_the_mcp_server_or_registers_tools` is the AST rule. **Source scan:** `test_no_source_file_but_the_admin_file_calls_a_delete_drop_index_or_command_method` scans every `*.py` under `src/`, at any depth, except the exact file `src/onb_mcp_mongo/db_users.py` for `.delete_*`, `.drop*`, `.create_index*`, `.command(`, `replace_one`, `bulk_write` and others, and asserts the scanned set covers the whole tool closure. **Admin code unreachable:** `test_the_tool_closure_holds_the_tool_code_and_not_the_admin_code` builds the AST import closure from `server.py`; it must contain every handler's and contract's module (plus tools, policy, validator and validator_rules) and must not contain `db_users`. `test_importing_the_tools_never_loads_the_admin_code` uses a fresh interpreter that imports every closure module and checks `db_users` is not in `sys.modules`. **The exemption itself:** `test_the_admin_exemption_is_exactly_the_provisioning_file` pins it to that one resolved path, and `test_the_package_has_no_subpackages` keeps the import closure complete. **Missing tools:** `test_calling_a_tool_that_does_not_exist_is_an_error` tries `delete_one`, `drop_collection`, `create_index`, `run_command` and `admin_command`. All 10 PASSED (`box-tests.txt`). Mutations V26–V34 fail them. |

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
| Check | `rtk proxy make check` (90ec295) | `make check: all passed`, exit=0. Lint, typecheck and test for 17 projects; mcp-mongo `509 passed` (`docs/evidence/MCP-2/make-check.txt`) |
| Integration | `rtk proxy make test-int` (rebased branch) | exit=0. `12`, `3`, `28`, `10`, `17`, `77`, `8` and `2 passed`; `Successfully ran target test-int for 7 projects` (`docs/evidence/MCP-2/make-test-int.txt`, compose project `ob-mcp-2` on 19500/19501) |
| Box tests | `uv run --frozen pytest apps/mcp-mongo/tests/test_validator.py …/test_server_validator.py …/test_validator_properties.py …/test_tool_surface.py -v` | `274 passed`; 164 corpus PASSED lines (82 payloads × 2 tests) (`box-tests.txt`) |
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
**All guards:** `docs/evidence/MCP-2/mutation-checks.txt` (script `docs/evidence/MCP-2/mutate.py`) gives `SUMMARY: 34 of 34 mutations caught, every file restored` (byte-for-byte sha256), run on the rebased branch.

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

| V32 | Mb: `run_command` defined in `db_users.py` and registered by `main.py` with `server.add_tool` | 2: `test_only_the_server_module_builds_the_mcp_server_or_registers_tools`, `test_the_server_main_serves_has_exactly_the_prd_tools` |
| V33 | Mb variant: `main.py` registers a tool with `@server.tool()` | the same 2 tests |
| V34 | Md: a new nested `helpers/db_users.py` with `.drop()`, imported by `tools.py` | 2: the source scan and `test_the_package_has_no_subpackages` |

V28–V34 test the narrowing:
- A destructive call in any file other than the one admin file fails.
- The admin module cannot become reachable from a tool, statically or dynamically.
- Nothing outside `server.py` can register a tool.
- What `main()` actually serves is checked.

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
None written. Choices made:
1. `$regex`, `$text` and `$regexMatch` are off for ReDoS reasons.
2. A field-path string in an expression is checked like a field, so `"$org_id"` is refused even though it only reads the caller's org. This keeps "org_id is never named" literal.
3. The validator runs in the server, not in each handler, so every tool is covered by one call.

## Traps hit
- Hypothesis random trees almost never pass a strict validator (about 1%), so the property was nearly vacuous. Grammar-built calls with 1-in-20 mutated keys fixed it.
- The MCP SDK client cannot serialise very deep JSON (pydantic depth limit), so deep payloads are sent as raw JSON-RPC.
- The rtk hook filters `make` output. Use `rtk proxy make …` to capture full gate logs.
- Raw confusable characters in test files trip ruff RUF001. Write them as `\u` escapes.
