# Handoff — HUB-1 · Route every agent tool call through one policy gateway        attempt 1 · goal/hub-1-mcp-hub · code at 321fd71

**Outcome claimed.** `apps/mcp-hub` is an MCP server (streamable HTTP, `/mcp`) that is the single
entry point for agent tools. For each call it:
1. verifies a short-lived agent token (`onb_agents.tokens`);
2. loads the agent's manifest from the registry;
3. checks the manifest, the token scopes, the JSON-schema contract, `org_id`, raw PII in arguments,
   collections and approvals, and hard-denies the vault;
4. injects tenant scope and credentials (a call context signed for this one call);
5. calls mcp-mongo;
6. scans the response for PII and credentials;
7. audits the call through `AuditLog.audit(...)` and traces it with one `mcp.hub.<tool>` span.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Expired token, tool outside the manifest, collection outside the manifest, and a vault collection under a forged manifest are each denied with an audit record and a `outcome=denied` span (named tests) | yes | **Unit** (in-memory `AuditLog`, in-memory exporter), `apps/mcp-hub/tests/test_gateway.py`: `test_an_expired_token_is_denied_with_an_audit_record_and_a_denied_span`, `test_a_tool_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span`, `test_a_collection_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span`, `test_a_vault_collection_under_a_forged_manifest_is_denied_with_an_audit_record_and_a_denied_span`. Each one asserts: exactly one audit record (actor `agent:db_agent`, action `mcp.tool_call`, outcome `denied`, reason = the code, org `org_acme`); one `mcp.hub.*` span with `outcome=denied`, `denial.reason`, `org_id`, `agent.name` and `run_id`; and that nothing was forwarded downstream. The forged manifest is a `model_construct` manifest that lists `pii_vault`, as if it had passed the registry; the hub's own hard deny refuses it (`vault_denied`). `test_policy.py::test_a_vault_collection_under_a_forged_manifest_is_hard_denied[pii_vault, pii_vault_aadhaar, org_keys, pii_access_log, users]`. **Integration** (testcontainers MongoDB, real mcp-mongo over the MCP-3 users, real `AgentRegistry` and `AuditLog`, the record read back from `audit_log` by the span's trace id): the four `test_hub_int.py::test_int_…` tests with the same names. In the integration run the forged record is written straight into `agent_registry` with its hash recomputed. The real registry re-validates it, so it is denied as `manifest_invalid`, with a record (target `tool:mongo.find/pii_vault`) and a denied span. Command: `bash docs/evidence/HUB-1/box-tests.sh` gives `23 passed`, `1 passed`, `12 passed`, exit=0 (`docs/evidence/HUB-1/box-tests.txt`). |
| An allowed call is scoped to the token's org even when its args name another org (test) | yes | The arguments name org B in values the contract allows, for example `{"owner": "org_globex"}` or a filter matching org B's documents. An explicit `org_id` key is denied (`org_id_not_allowed`), never honoured (PRD §7.5). **Unit:** `test_gateway.py::test_an_allowed_call_is_scoped_to_the_token_org_even_when_its_args_name_another_org` checks that the call context has `org_id=org_acme`, the args are forwarded unchanged, and the record is in org_acme with nothing in org_globex. `test_server.py::test_end_to_end_the_driver_sees_only_the_token_org` (hub → real mcp-mongo ASGI) checks that the driver filter carries `org_id: org_acme`. **Integration:** `test_int_an_allowed_call_is_scoped_to_the_token_org_even_when_its_args_name_another_org` seeds 1 org A and 2 org B docs that all match the filter and gets 1 doc back, with count 1. `test_int_an_update_naming_another_orgs_document_matches_nothing` gets `matched 0` and leaves org B's doc unchanged. (`box-tests.txt`) |
| A downstream response containing a raw mobile number is blocked or redacted before reaching the agent (test) | yes | Redacted to `[MOBILE]` (the PRD detectors, `onb_guardrails.pii`). **Unit:** `test_gateway.py::test_a_raw_mobile_in_a_downstream_response_is_redacted_before_it_reaches_the_agent` checks that the span has `pii.redacted=1` and no digits, and the record reason is `pii_redacted`. `test_scan.py::test_a_raw_mobile_number_is_redacted_from_structured_and_text_content[4 formats]`. `test_server.py::test_end_to_end_a_raw_mobile_from_mcp_mongo_is_redacted`. `test_review_fixes.py::test_a_number_holding_a_mobile_is_redacted_from_a_response`. **Integration:** `test_int_a_raw_mobile_stored_in_a_tenant_document_is_redacted_before_it_reaches_the_agent` uses a real Mongo document with a synthetic mobile; the note becomes `call [MOBILE] re laptop` and no span holds the digits. (`box-tests.txt`) |
| Downstream credentials never appear in tool responses or agent-side config (test) | yes | **What the agent side holds** is `onb_agents.tokens.hub_client_config(url, token)`: the hub URL and its own token, nothing else (`test_tokens.py::test_the_agent_side_config_holds_only_the_hub_url_and_the_agent_token`). **Responses:** any response holding a credential the hub has is withheld whole (`credential_in_response`). The credentials are the agent-token key, the call-context key, this call's signed context, the DB URI, and its password alone and as `user:password`. Tests: `test_gateway.py::test_a_downstream_credential_in_a_response_never_reaches_the_agent[downstream, context, token_key]`, `test_review_fixes.py::test_an_echoed_call_context_is_withheld_even_though_it_shares_nothing_with_the_key`, `test_main.py::test_the_gateway_main_builds_holds_every_credential_it_must_never_return` / `…password_alone…`. **End to end:** `test_server.py::test_end_to_end_credentials_are_injected_by_the_hub_and_never_returned` checks that the forwarded request carries `X-Onb-Call-Context`, carries no `Authorization`, and the response and agent config hold neither key nor context. **Integration:** `test_int_downstream_credentials_never_appear_in_responses_or_agent_side_config` covers an allowed call, a denial, a refusal and `tools/list` against the context key, DB URI and MCP user names. `test_int_a_stored_credential_is_withheld_whole` has the key stored in a document; it is withheld and audited. (`box-tests.txt`) |
| `make test-xt` covers every hub tool | yes | `make test-xt` runs `nx run-many -t test-xt`, which today is `apps/mcp-hub/tests_int/test_hub_xt.py`. The suite: two orgs on a throw-away MongoDB; a case per hub tool in `HUB_TOOL_CASES` (an org A token cannot read, list, count, update or infer org B, and an insert lands only in A); a mirror case for org B. `test_every_hub_tool_has_a_cross_tenant_case` enumerates the running hub's `tools/list` over MCP and fails on an uncovered or stale tool. Output: `make test-xt: hub tools covered: mongo__aggregate, mongo__count, mongo__find, mongo__insert_one, mongo__list_collections, mongo__update_one`, `10 passed`, `Successfully ran target test-xt for project mcp-hub`, exit=0 (`docs/evidence/HUB-1/make-test-xt.txt`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Agent token (orchestrator → hub).** `from onb_agents.tokens import AgentClaims, issue_agent_token, verify_agent_token, hub_client_config`. HS256, `iss=orchestrator`, `aud=mcp-hub`. Claims: `agent`, `run_id`, `org_id`, `scopes`, `iat`, `exp`, `jti`; lifetime ≤ 900 s. The key is env `AGENT_TOKEN_KEY` (≥ 32 characters, different from `MCP_CALL_CONTEXT_KEY`). `scopes` hold the hub tool names this task may use (`mongo__find`) plus human approvals (`approved:update_one:new_hires`). | `libs/py-agents/tests/test_tokens.py` (19 tests) |
| **Hub tools** are named `<server>__<tool>` (`mongo__find` …). The schemas are mcp-mongo's own contracts. The agent connects to `<hub>/mcp` with `Authorization: Bearer <token>`. | `test_server.py::test_the_hub_lists_every_catalog_tool_with_its_contract` |
| **A call is allowed only when the manifest AND the token scopes allow it.** The checks run in this order, and each denial is a tool error `denied (<reason>): <fixed text>`. Reasons: `no_token`, `token_invalid`, `token_expired`, `unknown_tool`, `unknown_agent`, `agent_retired`, `manifest_invalid`, `vault_denied`, `server_not_in_manifest`, `tool_not_in_manifest`, `scope_not_granted`, `invalid_arguments`, `org_id_not_allowed`, `raw_pii_in_arguments`, `collection_not_in_manifest`, `collection_not_provisioned`, mcp-mongo validator reasons (`forbidden_operator`, `pii_read_only`, …), `approval_required` (the message names the rule), `credential_in_response`, `unsupported_content`, `downstream_denied`. | `test_policy.py`, `test_gateway.py`, `test_review_fixes.py` |
| **Audit record shape (ADR 0006).** actor `agent:<name>`, action `mcp.tool_call`, target `tool:<server>.<tool>[/<collection>]`, outcome `success`/`denied`/`failed`, reason = the code, `trace_id` = the span's trace. Agent-chosen collection names become `/other`; `tool:unknown` is used for unserved names. A withheld write is `failed`. Forged or missing tokens have no audit record (no verifiable org); they get a span and a log line. | `test_gateway.py`, `test_review_fixes.py`, int read-backs |
| **Fail-closed.** An allowed call whose audit write fails returns no data. Registry, policy or downstream failures are audited `failed` and answered `<tool>: internal error`; the exception text is never returned. | `test_an_allowed_call_whose_audit_fails_returns_no_data`, `test_a_registry_failure_is_audited_traced_and_never_echoed` |
| **Run it:** `python -m onb_mcp_hub.main`. Env: `AGENT_TOKEN_KEY`, `MCP_CALL_CONTEXT_KEY`, `MCP_HUB_MONGO_URI` (the hub's own DB user: find on `agent_registry`, insert on `audit_log`), `MCP_HUB_MONGO_SERVER_URL`. Optional: `MCP_HUB_DATABASE`, `MCP_HUB_HOST` (127.0.0.1), `MCP_HUB_PORT` (8100), `MCP_HUB_ALLOWED_HOSTS`. | `test_main.py` |
| The eval runner reads hub records (`onb_evals.toolbox.AUDIT_QUERIES`, `audit_from_documents`) as `AuditEntry(org, tool, args={}, outcome)`, in call order. | `libs/py-evals/tests/test_hub_audit.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U8 @ MS-5 | compose (testcontainers), 2026-11-11 | contributes: access per agent is enforced server-side and every decision is audited | box 1 tests | no (not the last prover) |
| NF-TENANT @ MS-6 | same | contributes: hub tools in `make test-xt` | `make-test-xt.txt` | no |
| NF-SEC @ MS-6 | same | contributes: vault hard deny, credential withholding, PII redaction | `box-tests.txt` | no |
| SM-3 @ MS-6 | same | contributes | as above | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0; mcp-hub `110 passed`, py-agents `134 passed` (cov 97%) (`docs/evidence/HUB-1/make-check.txt`) |
| Integration | `make test-int` | `Successfully ran target test-int for 8 projects`, mcp-hub `22 passed`, exit=0 (`make-test-int.txt`, stack `ob-hub-1` on 18500/18501) |
| Cross-tenant | `make test-xt` | `10 passed`, all six hub tools covered, exit=0 (`make-test-xt.txt`) |
| Eval self-test | `make evals-selftest` | `27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, exit=0 (`make-evals-selftest.txt`) |
| Contracts | `make contracts-check` | `openapi.json and the TS client are up to date`, exit=0 (`make-contracts-check.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| MCP-1 | call-context contract (`sign_call_context`, `X-Onb-Call-Context`, ≤ 60 s, one tool) | yes. Every forwarded call signs a fresh context for its tool. `test_every_hub_tool_reaches_mcp_mongo_with_a_context_for_that_tool[6]` uses the real mcp-mongo. |
| MCP-2 | `validator.check_call(tool, args)` and its reasons | yes. It runs at the hub too, and its reason goes into the audit (`pii_read_only` in `test_end_to_end_a_denial_from_mcp_mongo_is_audited`). |
| MCP-3 | `READ_ONLY`/`READ_WRITE` collection sets; `provision` | yes. The hub refuses unprovisioned collections (`collection_not_provisioned`). The int suite runs mcp-mongo over provisioned users. |
| REG-1 | `AgentRegistry.get_agent` re-validates the record; deny on None, inactive or `ManifestError` | yes (`unknown_agent`, `agent_retired`, `manifest_invalid`, int forged record) |
| AUD-1 | `AuditLog.audit(...)`, fail-closed, `kind:ref` targets | yes. The hub writes only through it; the read model is unchanged. |
| OBS-1 | `onb_telemetry.testing`; scrubbing at export | yes. No span processor was added. |
| T-2 | safety-net plugin for every Mongo suite | yes. `tests_int/conftest.py` imports the five names; `test_safety_net_guard.py`. |

## Mutation checks
`docs/evidence/HUB-1/mutation-checks.txt` (script `docs/evidence/HUB-1/mutate.py --int`): `SUMMARY: 33 of 33 mutations caught`. Every file is restored byte for byte.

| Mutation | Guard broken | Test that failed |
|----------|--------------|------------------|
| M1 | token expiry off | `test_an_expired_token_is_denied_but_its_genuine_claims_are_kept_for_the_audit` |
| M2 | 15-min cap | `test_malformed_tokens_are_refused[too-long]` |
| M3 | expired claims dropped | the expired-token audit tests |
| M4 | vault hard deny | `test_a_vault_collection_under_a_forged_manifest_is_hard_denied[*]` |
| M5 | forbidden names kept in the context | `test_a_forged_manifest_never_puts_the_vault_into_the_call_context` |
| M6 | manifest tool check | `test_a_tool_outside_the_manifest_is_denied` |
| M7 | scope check | `test_a_tool_outside_the_token_scopes_is_denied` |
| M8 | manifest collection check | `test_a_collection_outside_the_manifest_is_denied` |
| M9 | provisioned check | `test_a_collection_no_mcp_database_user_holds_is_denied…` |
| M10 | JSON schema | `test_the_json_schema_contract_is_enforced_for_a_server_without_its_own_validator` |
| M11 | `org_id` | `test_org_id_is_refused_for_a_server_without_its_own_validator[key]` |
| M12 | argument PII | `test_raw_pii_in_arguments_is_denied[*]` |
| M13 | approvals | `test_an_approval_rule_is_surfaced_until_the_token_carries_the_approval` |
| M14 | agent identity | `test_a_token_for_another_agent_than_the_record_is_denied` |
| M15 | retired | `test_an_unknown_or_retired_agent_is_denied` |
| M16 | tenant from token | `test_an_allowed_call_is_scoped_to_the_token_org…` |
| M17 | credential block | `test_a_downstream_credential_in_a_response_never_reaches_the_agent[*]` |
| M18 | PII redaction | `test_a_raw_mobile_number_is_redacted…` |
| M19 | numbers | `test_a_number_holding_a_mobile_is_redacted_from_a_response` |
| M20 | hex exemption | `test_a_hex_string_outside_an_id_key_is_scanned` |
| M21 | the signed context is a secret | `test_an_echoed_call_context_is_withheld…` |
| M22 | denial audit | the box 1 tests |
| M23 | `outcome=denied` span | the box 1 tests |
| M24 | fail-closed audit | `test_an_allowed_call_whose_audit_fails_returns_no_data` |
| M25 | unexpected failures | `test_a_registry_failure_is_audited_traced_and_never_echoed` |
| M26 | agent text in the target | `test_agent_text_in_a_collection_name_never_reaches_the_audit_or_the_span[*]` |
| M27 | bearer from the header | `test_a_call_over_mcp_carries_the_agent_token_to_the_gateway` |
| M28 | DNS rebinding | `test_a_request_for_another_host_is_refused` |
| M29 | runner reads hub records | `test_hub_records_become_audit_entries…` |
| M30–M33 (real stack) | tenant, vault, PII, credentials | `test_int_an_allowed_call_is_scoped…`, `test_int_a_vault_name_in_a_pipeline…`, `test_int_a_raw_mobile…`, `test_int_a_stored_credential_is_withheld_whole` |

The first run had M11 surviving, because mcp-mongo's validator, run at the hub, also catches `org_id` for Mongo tools. A non-Mongo test now pins the hub's own check (and M10's).

RED before GREEN: `red-step1.txt` (token lib), `red-step2.txt` (policy), `red-step3.txt` (scan), `red-step4.txt` (gateway) and `red-step6.txt` (eval read-back) are all collection errors before the module existed.

## Review findings left open
A code review and a security review (general-purpose agents with reviewer briefs) found **no critical issues**. The code review rated one finding high: unexpected exceptions were unaudited and echoed to the agent. It is fixed in 3be0813, together with the medium and low fixes below:
- withheld writes are audited `failed`;
- a downstream internal error is `failed`;
- an echoed signed context is pinned by a real test;
- `$rename` to `org_id` is denied at the hub;
- the DB password alone is a secret;
- numbers are scanned;
- raw PII in arguments is denied;
- no agent text goes into audit targets or spans;
- hex ids are exempt only under id keys;
- the evals read-back tolerates unknown outcomes and keeps call order.

Left open:
- **medium** · `gateway.py:_forward` · the audit is written after the downstream call (the PRD §7.4 order). If the audit store fails after a write has committed, the agent gets "internal error" and there is no record, so a retry could duplicate the write. The mcp-mongo span still records the write. A pre-call "attempt" record for write tools would close this; it changes the record model (two records per write), so it is left for SEC-2/AUD follow-up.
- **medium** · replay: an agent token can be reused within its life (≤ 15 min), and the run is not checked for being active. A call context can be replayed within 60 s if intercepted on the private network (MCP-1 finding). Neither ever reaches an agent through the hub. A `jti` cache and an argument digest belong to SEC-2.
- **low** · `scan.py` · if two response keys redact to the same marker, one overwrites the other. Keys holding PII are themselves a leak, and the redaction still holds.
- **low** · `apps/mcp-hub` is not yet a compose service, and there is no `.env.example` entry for `AGENT_TOKEN_KEY`, `MCP_HUB_*` or `MCP_CALL_CONTEXT_KEY`. The card does not ask for deployment wiring (DEP-1 / the orchestrator goal).
- **low** · there is no request-body byte cap at the hub (MCP-2 suggestion). The MCP SDK refuses very deep JSON, and the hub's argument walk is capped at 10 000 nodes (`too_large`).

## Left undone, and why
- **`/goal`**: this environment cannot set it. The condition printed by `plan.py goal HUB-1` was shown in-session, and every box is proved above.
- **The vault check under a forged manifest in the integration run** stops at the registry (`manifest_invalid`), because the real registry never loads a manifest that names the vault. The hub's own hard deny behind it (`vault_denied`) is proved by the unit tests and mutation M4. In the int suite it is proved for a registered agent's pipeline (`test_int_a_vault_name_in_a_pipeline_is_hard_denied…`, M31).
- **Interpretation, box 2:** "args name another org" means values naming org B. An `org_id` key is denied, not silently overridden, because PRD §7.5 says "any `org_id` in the agent's filter is rejected".

## Found for other goals
- **T-3 (merge note for the orchestrator):** T-3's branch also defines `make test-xt` (pytest `tools/tests_xt`, needs `up`). Merge the two recipes: run T-3's suite and `$(NX) run-many -t test-xt`. In `tools/tests_xt/surface.py`, the hub is not a `FastMCP` (it uses the SDK's low-level `Server`), so `modules_calling("FastMCP")` will not list it. Enumerate its tools with `onb_mcp_hub.server.hub_tools(mongo_catalog())`; the cases live in `apps/mcp-hub/tests_int/test_hub_xt.py::HUB_TOOL_CASES`.
- **AG-1 / orchestrator:** issue one token per task with `issue_agent_token(AgentClaims(agent, run_id, org_id, scopes=<hub tool names for this task>), AGENT_TOKEN_KEY)`. Add `approved:<rule>` after a human approves. Map hub tool names (`mongo__find`) to the agent's tool list. When agents go through the hub, feed eval argument checks (`audit_no_allowed_call_matching`) from `capture.tool_calls`, because hub audit records hold no args (ADR 0006).
- **MOCK-1:** add the mocks server to `catalog.py` (its schemas) and `McpDownstream` (URL and credential). The hub's JSON-schema and `org_id` checks already cover servers without their own validator.
- **DEP-1 / SEC-2:** create the hub's DB user with find on `agent_registry` and insert on `audit_log` only. Add the hub to compose with `MCP_HUB_ALLOWED_HOSTS=mcp-hub`. Pre-call audit for writes. `jti` replay cache.
- **UI-8 / FR-A2:** recent denials = `GET /audit?action=mcp.tool_call&outcome=denied`.
- **EVAL follow-up:** retire the stand-in ToolBox's `kind: mcp_call` shape once adapters use the hub.

## Decisions forced (ADRs written)
- `docs/adr/0006-mcp-hub-tokens-audit-and-tool-names.md`: the agent token and its scopes, the tool-call audit record shape (settled with the eval runner), no audit for unverifiable orgs, and `<server>__<tool>` names.

## Traps hit
- The PRD's PII detectors match digit runs inside ObjectId hex and vault tokens. The scan exempts `ptk_` tokens and hex ids under id keys only.
- `AgentRegistry.sync` needs transactions (a replica set). The testcontainers Mongo is standalone, so the int suite writes records in sync's exact shape.
- FastMCP routes unknown tool names before any custom code runs, so they would go unaudited. That is why the hub uses the low-level `Server`.
- Under `make test-int`, 8 suites start containers at once; the hub's Mongo once missed a 60 s ping deadline. It is now 180 s.
- The output-filtering shell proxy truncated evidence logs; they were re-run through `rtk proxy`.
