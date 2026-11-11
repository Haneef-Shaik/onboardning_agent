# ADR 0006 — MCP hub: agent tokens, the tool-call audit record, and hub tool names

- **Status:** accepted (HUB-1), 2026-11-11
- **Goal:** HUB-1 · PRD §5.2, §7.4, §7.9, FR-A2; AUD-1 and EVAL-1 handoffs

## Context

HUB-1 makes `apps/mcp-hub` the single entry point for agent tools. Four things were open:

1. The agent token's shape and who verifies it (PRD §7.4 lists `agent`, `run_id`, `org_id`,
   `scopes`, `exp ≤ 15 min`, but not what `scopes` hold).
2. The shape of the tool-call audit record. Two shapes shared `audit_log`: EVAL-1's stand-in hub
   wrote `{kind: "mcp_call", tool, args_json, outcome: allowed|denied|error, result_org_ids}`,
   and AUD-1's `AuditLog` writes `{actor, action, target, outcome: success|denied|failed, reason,
   trace_id, at}`. `GET /audit` reads with `AuditEntry.from_document`, so an `mcp_call` document in
   a real org would 500 that page (AUD-1 handoff).
3. What to audit when the org is unknown.
4. Hub tool names.

## Decision

1. **Agent token** (`onb_agents.tokens`, used by the orchestrator to issue and by the hub to
   verify): HS256 JWS, `iss=orchestrator`, `aud=mcp-hub`, claims `agent`, `run_id`, `org_id`,
   `scopes`, `iat`, `exp`, `jti`; lifetime 1..900 s; key `AGENT_TOKEN_KEY` (≥ 32 characters,
   distinct from `MCP_CALL_CONTEXT_KEY`). `scopes` narrow the manifest for one task: hub tool names
   (`mongo__find`) and human approvals (`approved:<rule>`, e.g. `approved:update_one:new_hires`). A
   call is allowed only when the manifest **and** the scopes allow it. A `requires_approval` rule
   without its `approved:` scope is denied with `approval_required` and the rule named, so the
   orchestrator can raise the HIL task (PRD §6.3).
2. **Audit record** — the hub writes **only** through `AuditLog.audit(...)`, one record per call:
   actor `agent:<name>`, action `mcp.tool_call`, target `tool:<server>.<tool>[/<collection>]`
   (`tool:unknown` for a name the hub does not serve, so agent text is never stored), outcome
   `success` | `denied` | `failed`, reason the stable denial code (`vault_denied`,
   `tool_not_in_manifest`, `org_id_not_allowed`, the query validator's own reasons such as
   `forbidden_operator`, …) or `pii_redacted` / `downstream_unavailable`. `trace_id` links the record
   to the call's `mcp.hub.<tool>` span. **No arguments and no result orgs are stored**: AUD-1 forbids
   free text in `target`/`reason`, and arguments can hold free text. The read model is unchanged.
   The eval runner reads both shapes (`onb_evals.toolbox.AUDIT_QUERIES`, `audit_from_documents`):
   hub records map to `AuditEntry(org_id, tool, args={}, outcome)`.
3. **Unknown org.** A denial whose token is expired but genuinely signed is audited in the token's
   org (the signature proves the org). A missing, malformed or forged token has no verifiable org:
   the denial is a span (`outcome=denied`, `denial.reason=token_invalid|no_token`) and a log line,
   never a record in an org an attacker named.
4. **Tool names** are `<server>__<tool>` (`mongo__find`): agent-side model APIs (Bedrock) accept only
   `[A-Za-z0-9_-]` in tool names.

The audit is fail-closed: an allowed call whose record cannot be written returns no data.

## Consequences

- `GET /audit` can list hub records as they are; FR-A2's "recent denials" is a filter on
  `action=mcp.tool_call, outcome=denied`.
- Eval checks that read arguments from the audit (`audit_no_allowed_call_matching`) see `args={}`
  for hub records. The stand-in `ToolBox` still writes `mcp_call` documents to eval scratch
  databases only. **AG-1 / the eval adapters:** when an agent's tools go through the hub, feed
  argument checks from `capture.tool_calls` (the agent side), not the audit log, so they keep their
  power; and retire the stand-in's `mcp_call` shape.
- Replay of a call context within its 60 s life (MCP-1 open finding) stays open: each forwarded call
  gets a fresh context bound to one tool, and contexts never reach agents (responses holding one are
  withheld). Binding a context to one argument digest with a `jti` cache is left to SEC-2.
