# ADR 0014 — Admin views: where each view reads from, and the resolved model on agent records

- **Status:** accepted (UI-8), 2026-11-11
- **Goal:** UI-8 · PRD FR-A2, U8, §9, §10 (Registry & audit, Evals); ADR 0006 (HUB-1), ADR 0005 (UI-1)

## Context

UI-8 gives HR_ADMIN read-only views of agents, their recent denials, the audit log, the PII access log
and eval runs. Three questions were open:

1. Where an agent's "recent denials" come from, and whether they need a route of their own.
2. Manifests name a model *alias* (`llm: {alias: standard}`), but FR-A2 asks the UI to show each agent's
   *connector and model*. The alias only becomes a connector and model through `config/llm.yaml` and
   the environment.
3. Eval runs had no API route (PRD §10 lists `GET /evals/runs`, `GET /evals/runs/{id}`).

## Decision

1. **Denials reuse `GET /audit`; no new route.** The hub audits every refused call as actor
   `agent:<name>`, action `mcp.tool_call`, outcome `denied`, target `tool:<server>.<tool>[/<collection>]`,
   reason = the denial code (ADR 0006). An agent's page asks
   `GET /audit?actor_type=agent&actor_id=<name>&action=mcp.tool_call&outcome=denied&limit=10`, newest
   first. The audit route's role check, tenant scope and cross-tenant cases already cover it.
2. **The API resolves the model the way the agent runtime does.** `create_app(..., llm: LLMConfig | None)`;
   each `AgentRecord` gains `resolved_llm: {connector, model, alias, source} | null` from
   `onb_llm.resolve_llm(config, manifest=manifest.llm).task_record()`, the same record a task stores.
   Provider params (region, guardrail, fake script) are never part of it. `onb_api.main` loads
   `$CONFIG_DIR/llm.yaml` (TPL-2's required `CONFIG_DIR`) with the process environment at startup and fails fast (`LLMConfigError`) when it
   does not resolve, like every other service that reads it. Without a config (tests, the OpenAPI
   export) the field is `null` and the UI falls back to the manifest's own choice.
   No org override store exists yet; when one does, pass it to `resolve_llm` here too.
3. **`GET /evals/runs` and `GET /evals/runs/{run_id}`** read `eval_runs` (written by
   `onb_evals.results.record_run` under the results org) through `TenantRepository` in the token's
   tenant scope, HR_ADMIN only, newest first by `(started_at, _id)`, paged like `/audit`
   (`MAX_EVAL_RUN_SKIP`). The contract (`onb_contracts.evals`) carries the rates, not the harness's exact
   fractions. A stored run that does not read back raises (500), so a run is never shown partially.
   Run ids are checked against the harness's `evr_<UTC stamp>_<8 hex>` grammar (422 otherwise).

The web views live under `/admin/*` behind UI-1's nav-table guard (`RequireSection`), with their own
sub-nav (Agents, Audit log, PII access log, Eval runs). The PII access log shows values only through
`MaskedField` without a reveal control; the token column is not rendered.

## Consequences

- The API now needs the LLM environment (`LLM_CONNECTOR`, `LLM_MODEL_*`) at startup, as the workers do.
  `.env.example` already lists them. It reads `llm.yaml` from `CONFIG_DIR`, like the template validator.
- A manifest alias that llm.yaml lacks leaves only that agent's `resolved_llm` null (logged as a
  warning); the list still answers 200. REG-1's sync could validate aliases against llm.yaml so this
  never reaches the registry.
- Eval run ids must keep the harness's grammar; a test keeps `EVAL_RUN_ID_PATTERN` and
  `onb_evals.session._run_id` in step, and another the collection name.

## Rejected

- A dedicated `GET /agents/{name}/denials` route: duplicates `/audit` filtering and adds a cross-tenant
  surface for nothing.
- Showing only the alias: FR-A2 asks for connector and model, and the alias hides which model runs.
- Resolving in the browser: the browser has neither llm.yaml nor the environment, and must not.
