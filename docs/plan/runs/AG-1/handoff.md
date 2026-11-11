# Handoff — AG-1 · Run any registered agent with only its allowed tools        attempt 1 · goal/ag-1-agent-runtime · code at 4261dd5

**Outcome claimed.** `onb_agents.runtime` builds any registered agent from its manifest as a LangGraph
subgraph (`build_agent_graph`, run by `AgentRunner`). Its parts:
- **Model:** from LLM-1's `LLMRuntime.chat_model_for` (`runtime_models`). The task keeps `task_record()`.
- **Tools:** from the MCP hub through `langchain-mcp-adapters` (`HubToolSource`). Only the manifest's
  `<server>__<tool>` names are bound, and the manifest's local tools `date_calc` and `render` are added.
- **Guards and budgets:** every model call goes through GR-1's `GuardedChatModel`, with a step, token
  and time budget from the manifest `limits`, under the run budget.
- **Token and grant:** one agent token per task, scoped to the manifest's hub tools plus approved
  rules. The grant travels only in the hub connection's headers.
- **Output contract:** the final answer is validated against the agent's contract in
  `onb_contracts.agent_outputs` (`status: ok | refused | needs_human`), with one repair.

Outcomes are always visible (ADR 0010).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| With the `fake` connector, an agent calling a tool outside its manifest is denied by the hub and the denial appears in its trajectory (test) | yes | `libs/py-agents/tests/test_runtime_boxes.py::test_box1_a_tool_outside_the_manifest_is_denied_by_the_hub_and_the_denial_is_in_the_trajectory`. **Setup:** the real HUB-1 `Gateway` and MCP server run in process over streamable HTTP. The model is built by `LLMRuntime.chat_model_for` on `config/llm.yaml` with `LLM_CONNECTOR=fake` and a script file. The runtime's manifest lists `insert_one`, so it is bound and scoped into the token. The registry's `db_agent` does not list it. **The test asserts:** (a) the trajectory step `("mongo__insert_one", "denied", "tool_not_in_manifest")` with its args; (b) nothing was forwarded downstream; (c) the hub audit record is `denied` / `tool_not_in_manifest` with actor `db_agent`; (d) the model's next input contains `denied (tool_not_in_manifest)`. **Second test:** `test_box1_a_tool_the_agent_was_never_given_is_still_sent_to_the_hub_and_denied`. A model names unbound tools (the fake connector refuses scripts like that). Both calls are still sent to the hub, which denies them (`tool_not_in_manifest`, `unknown_tool`) and audits them. **Command:** `bash docs/evidence/AG-1/box-tests.sh` gives `2 passed` (`docs/evidence/AG-1/box-tests.txt`). |
| The grant and agent token never appear in captured LLM input (test) | yes | `test_box2_the_grant_and_the_agent_token_never_appear_in_captured_llm_input`. **Proved it travelled:** the token and the grant reached the hub as `Authorization` and `X-Onb-Pii-Grant` headers. **Proved the capture works:** the tool result reached the model. **Checked:** neither the token, nor the grant, nor any JWS segment of either appears in any LLM input. Input is read two ways: the fake model's own `calls()` log and a LangChain callback rendering, as the eval harness captures it. Neither appears in `result.to_dict()` either. **Other paths:** `test_box2_..._on_every_path[hub_denial_then_repair, repair_fails, step_budget, transport_failure]` run the same assertions over LLM input, `result.to_dict()`, captured logs (DEBUG) and every audit record. **Command:** `bash docs/evidence/AG-1/box-tests.sh` gives `5 passed`. **Mutations M8, M9, M11** put the grant into the instruction, put the token into the task input, and drop the header; each is caught. |
| An exhausted step budget ends the run with a visible failure (test) | yes | `test_box3_an_exhausted_step_budget_ends_the_run_with_a_visible_failure`. **Setup:** `max_steps=2`, and the script has three tool turns. **The test asserts:** outcome `failed`, `output is None`, `failure == ("budget_exceeded", "steps 2/2")`. The third model call is never made. The trajectory keeps `llm, tool, llm, tool`. A `guardrail.budget` audit record is `denied` / `steps 2/2`. `to_dict()["failure"]` holds the failure. **Command:** `box-tests.txt` gives `1 passed`. **Mutations:** M13 drops the step limit and M14 makes budget errors not failures; both are caught. |
| Every agent output contract has a schema test | yes | `libs/py-contracts/tests/test_agent_output_contracts.py`, 115 tests. **Per contract** (`template_builder` draft and suggest, `approvals_agent`, `db_agent`, `templates_agent`, `provisioning_agent`, `run_assistant`), it checks: - an `ok` answer validates and round-trips; - a refusal validates; - an unknown field, an unknown status, an `ok` answer without its payload, and a wrongly typed payload are each refused; - a refusal built from a code is valid; - the JSON schema has `additionalProperties: false` and the three statuses; - a refusal or hand-off must explain itself; - a refusal carries no payload. **Payload bounds:** task-update bounds, DB-answer bounds with no `org_id`, and `ptk_` recipients. **Coverage of agents and outputs:** `test_every_contract_has_a_schema_case` pins the contract set. `test_every_scripted_eval_output_validates_against_its_agents_contract` validates all 27 eval self-test outputs. `test_runtime.py::test_every_registered_agent_has_a_contract_and_runs_from_its_manifest[6 agents]` covers every `config/agents/*.yaml`. **Command:** `box-tests.txt` gives `115 passed`, then `6 passed`. **Mutations:** M38–M48 are all caught. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Run a task:** `AgentRunner(models=runtime_models(llm_runtime, org_overrides=<org_id → LLMSelection \| None>), tools=HubToolSource(hub_url), auditor=AuditLog(store), token_key=env["AGENT_TOKEN_KEY"]).run(registered_agent, AgentTask(org_id, run_id, task_id, input, mode=None, approvals=(), grant=None), run_budget=Budget(...))` returns an `AgentRunResult`. It runs inside `tenant_scope(task.org_id)`. Store `result.to_dict()` on the task: it holds outcome, output, failure, refusal, trajectory, the `llm` record and usage, and never a token, grant or tool result. | `test_runtime.py`, `test_runtime_boxes.py` |
| **Outcomes:** `completed` with a contract-valid `output`. `completed` with the contract's refusal and `refusal="injection_blocked"` when GR-1 blocks an injection, in the input or in a tool result. Otherwise `failed` with `failure.kind`: `budget_exceeded` (steps or tokens), `timeout` (any time limit, reason as `seconds 1.2/1` or `run seconds …`), `invalid_output` (after one repair, or `no_repair_budget`), `guardrail_blocked` (external guard), or `error` (exception type, e.g. `ConnectError`). A failed result has `output=None` and keeps the trajectory so far. **Caller errors raise before the task starts:** a retired agent, no contract, an approval not in `requires_approval`, an unknown local tool, or a missing connector. | `test_runtime.py`, `test_runtime_review_fixes.py` |
| **Trajectory:** `TrajectoryStep(kind="llm", tool_calls=…)` and `TrajectoryStep(kind="tool", name, args, outcome="allowed"\|"denied"\|"error", reason)`. A hub denial records the hub's reason code. Calls the hub denied are steps too. A reply is capped at 8 tool calls; the rest are `error / too_many_tool_calls` and never run. | box 1, `test_tool_calls_past_the_per_turn_cap…` |
| **Token scopes:** `task_scopes(manifest, approvals)` gives the manifest's hub tools plus `approved:<rule>` for each approved rule the manifest requires. One fresh token is issued per task. | `test_task_context.py` |
| **Grant:** sent only as the header `X-Onb-Pii-Grant` (`GRANT_HEADER`) on the agent's hub connection. | box 2 |
| **Output contracts:** `onb_contracts.agent_outputs.contract_for(agent, mode)` / `OUTPUT_CONTRACTS`. `Contract.refusal(code)` builds a refusal. The payload models `ChangeRequest`, `ApprovalPacket` and `Message` now back `onb_evals.contracts`. `bounded_json` (in `onb_contracts.templates`) is public. | box 4 |
| **Injection points for eval adapters (TB-1, AGT-1, HIL-1, CHAT-1):** a `ModelProvider` `(manifest, task) -> ModelChoice(record, model)` can return `invocation.chat_model`. A `ToolSource` `open(manifest, task, token) -> ToolSet(bound, forward)` can wrap the harness's tools. | `test_runtime_review_fixes.py::StaticTools`, box 1 second test |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none: AG-1 is in no ledger row; `grep AG-1 docs/plan/ACCEPTANCE.md` gives 0 matches) | | | | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. `Successfully ran target test for 17 projects`, `test-xt for project mcp-hub`. py-agents `553 passed` (cov 99%), py-contracts `296 passed` (`docs/evidence/AG-1/make-check.txt`) |
| Integration | `make test-int` | `Successfully ran target test-int for 8 projects`, exit=0 (`make-test-int.txt`, stack `ob-ag-1` on 19100/19101) |
| Cross-tenant | `make test-xt` (inside `make check`) | passed in `make check`. AG-1 adds no API route or MCP server module, so there are no new ROUTE_CASES or TOOL_CASES rows. |
| Eval self-test | `make evals-selftest` | `27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS`, exit=0 (`make-evals-selftest.txt`) |
| Contracts | `make contracts-check` | `openapi.json and the TS client are up to date`, exit=0 (`make-contracts-check.txt`) |
| Plan | `python3 scripts/plan/plan.py check` | `plan ok: 67 goals, 34 ledger rows` |
| Mutations | `uv run --frozen python docs/evidence/AG-1/mutate.py` | `SUMMARY: 48 of 48 mutations caught` |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| LLM-1 | `LLMRuntime.chat_model_for(manifest=LLMSelection(...), org_override=...)`; the `fake` connector with `LLM_FAKE_SCRIPT_FILE`; `ScriptedChatModel.calls()`; `task_record()` | yes (the box tests build models exactly this way; `result.llm == {connector: fake, model: fake-standard, alias: standard, source: manifest}`) |
| GR-1 | `GuardedChatModel`, `trusted_message`, `DecisionRecorder`, the error types, the budget semantics | yes. One system prompt; the task input stays an untrusted `HumanMessage` (delimited, mutation M21); the repair goes through `ainvoke`. |
| HUB-1 | `issue_agent_token(AgentClaims(...))`; tools named `<server>__<tool>`; `denied (<reason>)` tool errors; audit records | yes (box 1 reads the hub's own audit record) |
| REG-1 | manifests, `RegisteredAgent.active`, `limits`, `tools` | yes |
| EVAL-1 | `DEFAULT_CONTRACTS` entries replaced by the real models | yes. `libs/py-evals` tests pass. One test fixture's "good" message used `recipient_token: "t"`; it is now `ptk_seed_t`, because a recipient must be a PII token. |

## Mutation checks
`uv run --frozen python docs/evidence/AG-1/mutate.py` gives `SUMMARY: 48 of 48 mutations caught` (`docs/evidence/AG-1/mutation-checks.txt`). Every file is restored byte for byte.

| Mutations | Guard |
|-----------|-------|
| M1–M7 (box 1) | forwarding unbound names to the hub; denial classification; tool args in steps; the steps themselves; only manifest tools bound; token scopes; approval rules |
| M8–M12 (box 2) | grant into the instruction; token into the input; grant hidden from repr; grant header sent; failure reason is a type, not text |
| M13–M19 (box 3) | step limit; budget error is a failure; run budget as parent; one deadline over the whole task; failed means no output; real cause of a transport failure; state survives a failure |
| M20–M28 | guard on every call; input stays untrusted; injection is a refusal; tenant scope; contract validation; one repair; schema failure is `invalid_output`; repair needs a step; repair prompt is sanitised |
| M29–M37 | per-turn cap; one call per visit; safe tool names; local tools from the manifest; render does no re-render and allows plain names only; date bounds; bad args and out-of-range dates become error messages |
| M38–M48 (box 4) | ok carries its payload; extra fields forbidden; status enum; recommendation enum; Run Assistant refusal; mode selects contract; refusals explain themselves; a refusal has no payload; no `org_id` in DB answers; `ptk_` recipients; task-update bounds |

RED before GREEN: `docs/evidence/AG-1/red-step{1-contracts,2-local-tools,3-task-context,4-runtime-boxes,5-review-fixes}.txt` (collection errors before each module existed).

## Review findings left open
A code review and a security review were run by general-purpose agents with reviewer briefs.
- **Code review:** found 2 high, 6 medium and 2 low issues.
- **Security review:** found 0 critical, 0 high, 3 medium and 4 low issues.
- **Fixed:** both highs and every medium except the ones below. The fixes are in commits `fix(AG-1): review fixes …` and `test(AG-1): box 2 holds on …`.

Left open:
- **low** · `trajectory.py:tool_step`: denials are classified by the hub's `denied (<reason>)` text prefix. The hub marks some cases this way even when they are not plain denials: a write that ran but whose response the hub withheld (`ran=True`) also reads as `denied`. The fix is a structured outcome in the hub's result (`_meta`), which is a HUB-1 change, left for SEC-2.
- **low** · trajectory `args` are stored as the model sent them, after GR-1's output redaction. Free text the detectors miss (a name) can stay. Steps hold args because eval argument checks need them (ADR 0006, EVAL-1). A projection or size cap is left for SEC-1.
- **low** · `approvals` are trusted as given. ADR 0010 §7 says only the orchestrator builds `AgentTask`, from persisted HIL approvals (HIL-1). Signed approvals are left for SEC-2.
- **low** · `AgentTask.input` is a `Mapping` inside a frozen dataclass (it is not copied).
- **low (code review 10)** · `ok` with an empty payload (`answer: {}`, `provisioned: []`) is accepted. An empty DB answer can be legitimate.

## Left undone, and why
- **`/goal`:** this environment cannot set it. The condition from `plan.py goal AG-1` was printed in-session, and every box is proved above.
- **Eval adapters are not registered here.** An adapter is the agent's prompt and behaviour, which each agent's own goal owns (EVAL-1's `adapters.py` docstring names TB-1, AGT-1, HIL-1, CHAT-1). The runtime gives them `ModelProvider` and `ToolSource` to inject `invocation.chat_model` and the harness tools (ADR 0010 §8). The self-test still uses `ScriptedAgent`, and `make evals-selftest` passes.
- **The hub does not yet forward `X-Onb-Pii-Grant` to sinks.** The runtime sends it. MOCK-1 and NOTIF-1 add the forwarding and verification.
- **No checkpointing of the subgraph.** RUN-* embeds it in the run graph with the Mongo checkpointer.

## Found for other goals
- **MOCK-1 / NOTIF-1:** forward `X-Onb-Pii-Grant` from the agent's connection to the sink in the hub's downstream call, and never to Mongo. Add the `notify` and `mocks` servers to the hub catalog. Until then `templates_agent` and `provisioning_agent` get only their Mongo tools bound, because the hub lists no others.
- **TB-1, AGT-1, HIL-1, CHAT-1:** register `onb_evals.adapters.register_adapter(agent, factory)`. The factory should build an `AgentRunner` whose `ModelProvider` returns `ModelChoice(record, invocation.chat_model)` and whose `ToolSource` wraps the harness tools. Eval safety cases tb-006 and ra-007 come back as `refusal="injection_blocked"` with the contract's refusal (`status: refused`).
- **RUN-3 / orchestrator:** issue tasks with `AgentTask(..., grant=GrantIssuer(...).issue_grant(...))`. Put `approvals` only from HIL records. Keep `result.to_dict()` on the task. Run with `run_budget` for the per-run token budget.
- **SEC-2:** a structured denial outcome in hub results; signed approvals.
- **py-agents tests import `onb_mcp_hub`** (test-only; the hub depends on py-agents, not the reverse). The nx `test` inputs of py-agents now include `apps/mcp-hub/src`, `apps/mcp-mongo/src`, `config/agents` and `config/llm.yaml`. The py-contracts inputs include `evals/datasets` and `evals/selftest`.

## Decisions forced (ADRs written)
- `docs/adr/0010-agent-runtime.md`. The hub is the only tool authority (unbound names are forwarded). The grant travels as a header. Contracts and refusals are defined there. Outcome kinds, one deadline, the repair budget, one call per step with a per-turn cap, and who builds approvals are settled. Eval adapters belong to the agent goals.

## Traps hit
- **Fake connector:** it refuses scripted tool calls that name an unbound tool. That is why box 1 has a fake-connector test with a wider runtime-side manifest, plus a test whose model names unbound tools.
- **MCP transport failure:** a failure in the streamable-HTTP client's writer cancels the host task inside an anyio task group. The real cause arrives as an `ExceptionGroup`, and the state streamed so far would be lost. The runner keeps a progress holder and unwraps the group's leaf.
- **StructuredTool validation:** `StructuredTool` validates `args_schema` before the function runs. Without `handle_validation_error`, bad arguments crash the task instead of answering the model.
- **Combined pytest runs:** running three packages' `tests/` dirs in one pytest call collides on the `tests` package name. Run them per project, as nx does.
