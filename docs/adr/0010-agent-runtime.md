# ADR 0010 — Agent runtime: hub authority, out-of-band grants, contracts and visible outcomes

- **Status:** accepted (AG-1), 2026-11-11
- **Goal:** AG-1 · PRD §5.2, §7.1 (Sub-agents, Budgets), §7.10, §8.1, §8.2; ADR 0006 (HUB-1)

## Context

AG-1 builds any registered agent from its manifest as a LangGraph subgraph. Five things were open:

1. What the runtime does when the model calls a tool it was not given.
2. How the PII grant travels "in the tool-call context, out of band" (PRD §7.10).
3. Where the agent output contracts live, and what a guard-forced refusal looks like.
4. How a task ends when a guard or limit stops it.
5. Who registers eval adapters.

## Decision

1. **The hub is the only authority on tools.** The runtime binds only the hub tools its manifest
   names (`<server>__<tool>`) plus the manifest's local tools, and issues one token per task whose
   scopes are exactly those hub tools plus `approved:<rule>` for rules a human approved (an approval
   for a rule the manifest does not require is refused). A tool call for **any other name** is still
   sent to the hub over the same MCP session, never refused locally, so the hub denies it, audits it
   and the denial (`denied (<reason>)`) comes back as an error `ToolMessage` and a `denied` trajectory
   step. The same holds when the runtime's copy of the manifest is wider than the registry's: the hub
   checks the registry at call time.
2. **The grant is a header.** `AgentTask.grant` is sent only as `X-Onb-Pii-Grant` on the agent's hub
   connection, next to `Authorization: Bearer <agent token>`. Neither is ever put in a message, the
   graph state, the trajectory or the result; `AgentTask` keeps the grant out of its repr. The hub
   (HUB-1) does not forward the header yet: the sink-side goals (MOCK-1/NOTIF-1) add that.
3. **Contracts live in `onb_contracts.agent_outputs`**, one per agent (and per Template Builder
   mode), shapes from `evals/README.md`; `provisioning_agent` has no README row and gets
   `{status, provisioned: [{action, done, reference?}]}`. `ok` requires the payload field; `refused`
   and `needs_human` may leave it out and explain. Every model forbids unknown fields. Each contract
   builds its own refusal (`Contract.refusal(code)`), so a guard-forced refusal is contract-valid. The
   eval harness's interim payload models now come from here.
4. **Outcomes.** `completed` with a validated output; `completed` with the contract's refusal when the
   injection guard blocks (`refusal: "injection_blocked"`, GR-1); otherwise `failed` with
   `failure.kind` = `budget_exceeded` | `invalid_output` | `guardrail_blocked` | `timeout` | `error`
   and a reason of codes and counts (or an exception type). A failed result never carries output.
   The final reply gets one repair through the guards, recorded as `guardrail.output.schema`.
5. **Eval adapters are registered by each agent's own goal** (TB-1, AGT-1, HIL-1, CHAT-1), which owns
   the agent's prompt and behaviour. They pass `invocation.chat_model` through a `ModelProvider` and
   the harness's tools through a `ToolSource`; the runtime takes both by injection.

## Consequences

- Hallucinated tool names cost a hub round trip and an audit record; that is the point (FR-A2 denials).
- `AgentRunResult.to_dict()` is safe to store on the task and to feed eval captures.
- The runtime does not checkpoint the subgraph; RUN-* goals embed it in the run graph with
  `langgraph-checkpoint-mongodb`.
