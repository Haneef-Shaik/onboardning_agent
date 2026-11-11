# Handoff — MOCK-1 · Provide thin Bitbucket and provisioning mocks behind the hub        attempt 1 · goal/mock-1-integration-mocks · code at 2e0a8e4

**Outcome claimed.** `apps/mcp-mocks` serves deterministic, idempotent `create_bitbucket_account` and
`provision_laptop` tools. They are reachable only through the hub, with a hub-signed call context
for audience `mcp-mocks` signed with mcp-mocks' own key. The package also provides
`onb_mcp_mocks.telegram`, a Telegram transport stub for NOTIF-1's sink. No code in the package can
call a real external API.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| The `provisioning_agent` token can call the mocks through the hub (integration test) | yes | **Integration test.** Setup: testcontainers MongoDB; the real `AgentRegistry` holding the six `config/agents` manifests; the real `AuditLog`; real mcp-mocks and the hub served by uvicorn on loopback. Tests in `apps/mcp-hub/tests_int/test_hub_mocks_int.py`: `test_int_the_provisioning_agent_token_creates_a_bitbucket_account_through_the_hub` (result `status=active`; one new mock record; hub span `outcome=ok`, `mcp.server=mocks`; one audit record read back from `audit_log` by trace id, actor `agent:provisioning_agent`, target `tool:mocks.create_bitbucket_account`, outcome success; an `mcp.mocks.*` span for the run), `test_int_the_provisioning_agent_token_orders_a_laptop_through_the_hub`, and `test_int_no_downstream_credential_reaches_the_provisioning_agent`. **Unit tests** (hub → real mcp-mocks ASGI): `apps/mcp-hub/tests/test_mocks.py::test_the_provisioning_agent_calls_a_mock_through_the_hub[both tools]` and `::test_the_hub_signs_mocks_calls_with_the_mocks_key_for_the_mocks_audience`. **Command:** `bash docs/evidence/MOCK-1/box-tests.sh` → Box 1 `3 passed` (int), `3 passed` (unit); output in `docs/evidence/MOCK-1/box-tests.txt`. |
| Replaying a call with the same idempotency key returns the same result and creates nothing new (test) | yes | The idempotency key is the run task's id, the `task_id` argument (ADR 0011 §1). **Integration test:** `test_int_replaying_with_the_same_idempotency_key_returns_the_same_result_and_creates_nothing_new`. It makes two calls under two runs. It checks: identical `structuredContent` and text; the record count unchanged after the replay; `creations` +1 in total; the replay's mocks span `mock.replayed=True`; both audit records `success`. Also `test_int_the_same_idempotency_key_with_other_arguments_is_refused_and_creates_nothing` (audit `denied/downstream_denied`). **Unit tests:** `test_mocks.py::test_replaying_through_the_hub_…`, `::test_a_conflicting_replay_through_the_hub_…` and `::test_the_same_idempotency_key_in_another_org_…`; `apps/mcp-mocks/tests/test_tools.py::test_replaying_with_the_same_idempotency_key_returns_the_same_result_and_creates_nothing_new[both]` and `::test_results_are_deterministic_across_restarts`; `test_server.py::test_replaying_a_call_through_mcp_…`; `test_review_fixes.py::test_concurrent_first_calls_with_one_key_create_one_record` (20 concurrent calls → 1 record) and `::test_group_order_does_not_make_a_replay_a_conflict`. **Command:** `box-tests.sh` → Box 2 `2 passed` (int), `3`, `7`, `4 passed` (unit). |
| An agent without the tool in its manifest is denied (test) | yes | **Integration test** (real registry): `test_int_an_agent_without_the_mock_tool_in_its_manifest_is_denied` covers `db_agent` with a token scoped to the mock tool → `server_not_in_manifest`. It checks: one audit record (denied, reason, target `tool:mocks.create_bitbucket_account`); a denied hub span; no mock record; no `mcp.mocks.*` span, so nothing was forwarded. `test_int_every_registered_agent_without_the_mock_tool_is_denied` covers `approvals_agent`, `run_assistant`, `template_builder` and `templates_agent`. `test_int_a_provisioning_token_without_the_scope_is_denied` → `scope_not_granted`. **Unit test:** `test_mocks.py::test_an_agent_whose_manifest_lacks_that_mock_tool_is_denied`, where the provisioning manifest is narrowed to `provision_laptop` → `tool_not_in_manifest`, with nothing forwarded. Also `::test_an_agent_with_no_mock_tools_in_its_manifest_is_denied` and `::test_a_token_without_the_mock_scope_is_denied`. **Command:** `box-tests.sh` → Box 3 `3 passed` (int), `3 passed` (unit). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Hub tool names are `mocks__create_bitbucket_account` and `mocks__provision_laptop`, matching REG-1's `provisioning_agent.yaml` (no YAML changed) | `test_mocks.py::test_the_provisioning_manifest_names_only_tools_the_hub_serves` |
| Every mock call takes `task_id` (24-hex run task id) as its idempotency key. A replay returns the identical result; the same key with other args is refused (`idempotency_conflict` → hub `downstream_denied`). Records are per org | `test_tools.py`, `test_mocks.py`, int box 2 |
| Mock ids are deterministic across restarts (SHA-256 of org, tool, task_id and argument fingerprint) | `test_tools.py::test_results_are_deterministic_across_restarts`, `test_review_fixes.py::test_ids_differ_…[both]` |
| mcp-mocks accepts only contexts for audience `mcp-mocks` signed with `MCP_MOCKS_CONTEXT_KEY` that name the tool being called | `test_server.py` (no / forged / mongo-audience / other-tool contexts → `no_call_context` / `context_tool_mismatch`) |
| `McpDownstream(urls, context_key, keys={...})` signs each server with its own key for audience `mcp-<server>`. It refuses to build when a non-mongo server has no key, or when keys repeat | `test_downstream.py` |
| Hub settings need `MCP_MOCKS_CONTEXT_KEY` (≥32 characters, distinct from the other two keys) and `MCP_HUB_MOCKS_SERVER_URL` | `apps/mcp-hub/tests/test_main.py` |
| `TelegramTransport.send_message(chat_id, text, *, inline_keyboard)` returns a `SentMessage(message_id, date)`. It checks Bot API limits and keeps no copy of the recipient or text | `apps/mcp-mocks/tests/test_telegram.py` |
| No module in `onb_mcp_mocks` imports an HTTP client, socket or vendor SDK | `test_no_external_calls.py` |
| mcp-mocks is in the cross-tenant suite: `tools/tests_xt` has `MCP_SERVERS`, `MCP_SERVER_MODULES` and `TOOL_CASES` rows, and hub `HUB_TOOL_CASES` covers the mock tools | `make check` → tests-xt `91 passed`, hub-xt covers 8 tools |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| none: `plan.py show MOCK-1` lists no ledger rows | n/a | n/a | n/a | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| check | `make check` (includes test-xt) | `make check: all passed`, exit=0; tools/tests_xt `91 passed`; hub test-xt `12 passed` (`docs/evidence/MOCK-1/make-check.txt`) |
| test-int | `make test-int` | `Successfully ran target test-int for 8 projects`, exit=0; mcp-hub int `32 passed` (`docs/evidence/MOCK-1/make-test-int.txt`). On the first run, mcp-mongo's testcontainer never answered its startup ping under parallel load (77 setup errors, `ServerSelectionTimeoutError`). Re-run alone: `79 passed`. Full re-run: green. |
| box tests | `bash docs/evidence/MOCK-1/box-tests.sh` | all groups passed (`box-tests.txt`) |
| mutations | `uv run --frozen python docs/evidence/MOCK-1/mutations.py` | `21/21 mutations killed` (`mutations.txt`) |
| contracts-check / evals-selftest | not run | the diff touches no contracts and no eval code |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| HUB-1 | MOCK-1 adds its servers to `catalog.py` and `McpDownstream`; the hub's schema and `org_id` checks cover servers without their own validator | yes: `invalid_arguments` for an `org_id` arg (`test_an_org_id_argument_to_a_mock_is_denied_at_the_hub`) |
| HUB-1 / ADR 0006 | one audit record per call through `AuditLog`, target `tool:<server>.<tool>`; refusals vs failures told apart by `<tool>: internal error` | yes: mocks follows the convention; int tests read the records back |
| REG-1 | `provisioning_agent.yaml` grants `mocks: [create_bitbucket_account, provision_laptop]` | yes: names match |
| MCP-1 | call-context contract (`X-Onb-Call-Context`, HS256, ≤60 s, names one tool) | yes: reused, with an added `audience` keyword (default unchanged) |
| T-3 | new MCP server → `surface.py` + `TOOL_CASES` | yes: `test_every_mcp_server_in_the_code_is_enumerated` was red until it was registered |

## Mutation checks
`docs/evidence/MOCK-1/mutations.txt`, 21/21 killed. Highlights:
- M1: replay creates a new record → `test_tools.py::test_replaying_…[create_bitbucket_account-args0]` and `test_mocks.py::test_replaying_through_the_hub_…` FAILED.
- M3: org dropped from the record key → `test_the_same_key_in_another_org_…` FAILED.
- M4: mocks verifies with the mongo audience → `test_server.py::test_a_call_with_a_context_returns_the_structured_result` FAILED.
- M5: tool binding removed → `test_a_context_naming_another_tool_is_denied` FAILED.
- M7: one key for every server → `test_downstream.py::test_each_server_gets_a_context_signed_with_its_own_key…` FAILED.
- M9: manifest tool check skipped → `test_an_agent_whose_manifest_lacks_that_mock_tool_is_denied` FAILED.
- M10: server check skipped → `test_an_agent_with_no_mock_tools_in_its_manifest_is_denied` FAILED.
- M13: stub keeps the text → `test_the_receipt_and_the_stub_hold_no_copy…` FAILED.
- M17: downstream falls back to the mongo key → `test_a_non_mongo_server_without_its_own_key_is_refused_at_construction` FAILED.
- M18: one quota for every org → `test_one_org_filling_its_quota_does_not_block_another_org` FAILED.
- M19 first survived: the restart-id test covered `account_id` only. The test was parametrized over both tools, and M19 is now killed.

## Review findings left open
- **low** · `apps/mcp-mocks/src/onb_mcp_mocks/tools.py` · ids are an unkeyed SHA-256: someone who knows an org_id, task_id and the args can predict a mock id. They reveal nothing across orgs and are mock data; an HMAC can come with a real adapter.
- **low** · `apps/mcp-mocks/src/onb_mcp_mocks/server.py` · DNS-rebinding protection is off, as in mcp-mongo. Calls still need a hub-signed context.
- **low** · the conflict refusal is lost across a mocks restart (in-memory store). Since ids include the argument fingerprint, one id never names two calls (ADR 0011 §3).
- **low** · the Telegram stub counts Python characters, not Telegram's UTF-16 units (commented in the code).

Fixed from the reviews:
- per-org quota with a `store_full` refusal;
- `McpDownstream` fails closed when a server has no key, and all keys must differ;
- result-contract errors are internal errors, not `invalid_arguments`;
- ids bound to the arguments;
- `groups` order-free;
- a thread lock that works across event loops;
- group chat ids accepted;
- tighter denial assertions.

Code review and security review found no critical or high issues.

## Left undone, and why
- **`/goal`**: this environment cannot set it. The condition from `plan.py goal MOCK-1` was printed in the session, and every box is proved above.
- **Compose service and `.env.example` entries** for mcp-mocks and the hub (`MCP_MOCKS_CONTEXT_KEY`, `MCP_HUB_MOCKS_SERVER_URL`): the hub itself is not a compose service yet (HUB-1 left it to DEP-1), and the card asks for no deployment wiring.
- **`mock_outbox`, the `notify` tool and the webhook** belong to NOTIF-1. MOCK-1 gives NOTIF-1 the transport seam only.

## Found for other goals
- **DEP-1:** add `mcp-mocks` (`python -m onb_mcp_mocks.main`, port 8200, `MCP_MOCKS_CONTEXT_KEY`) to compose. Give the hub `MCP_MOCKS_CONTEXT_KEY` and `MCP_HUB_MOCKS_SERVER_URL=http://mcp-mocks:8200/mcp`. All three hub keys must differ.
- **AGT-1 / RUN-3:** pass the run task's ObjectId hex as `task_id`. Retries are then safe. A changed argument set for the same task is refused, so do not retry with different args.
- **NOTIF-1:** use `onb_mcp_mocks.telegram.TelegramTransport` / `TelegramMockTransport` in the sink, after grant check, detokenise and substitute. Store the masked copy in `mock_outbox` yourself; the receipt has only `message_id` and `date`. `InlineButton(text, callback_data ≤ 64 bytes)` matches the `callback_query` contract.
- **SEC-2:** per-call-context replay is still open (unchanged from HUB-1). Also consider an HMAC for mock ids.
- **Integration (orchestrator):** this branch changes `McpDownstream`'s constructor contract: non-mongo servers now need `keys=`. It also changes hub `load_settings`, which now requires two new env vars. A parallel branch that builds `McpDownstream` with a non-mongo URL, or loads hub settings, must follow suit.

## Decisions forced (ADRs written)
- `docs/adr/0011-mcp-mocks-idempotency-and-per-server-contexts.md` covers four decisions:
  - `task_id` is the idempotency key (and why it is not named `idempotency_key`: the hub's PII exemption is for `*_id` keys; measured 1% false refusals otherwise);
  - one key and one audience per downstream server, failing closed;
  - the in-memory store with a per-org quota;
  - the Telegram stub is a library seam, not an MCP tool.

## Traps hit
- The hub's raw-PII argument check flags about 1% of random 24-hex strings under non-id keys (it measured 211 of 20 000), so an `idempotency_key` argument would have failed intermittently.
- FastMCP's `StreamableHTTPSessionManager.run()` runs once per instance. In-process tests that call twice need a fresh server over the same store.
- The hub's audit list is newest-first. Assert on the set of outcomes, not their order.
- `make test-int` starts 8 suites' containers at once. Under parallel load from other sessions, one container can miss its ping deadline. Re-run before blaming code.
