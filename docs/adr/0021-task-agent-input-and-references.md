# ADR 0021 — What a task agent is asked: opaque references, hire variables, and the templates agent's read

- **Status:** accepted (AGT-1), 2026-11-12
- **Goal:** AGT-1 · PRD §7 Agent Hub, §7.3 (manifests), §7.10 (PII), §8.2, §13; ADR 0019 (agent tasks in
  runs, which left "the hire's values never reach an agent" as the input rule), MOCK-1 (mock contracts)

## Context

Running the seed template through the real hub and mocks (AGT-1 boxes 2 and 3) showed that the agent's
input from ADR 0019 (action, template `inputs`, hire id) cannot drive the integrations:

- The mocks take a **24-hex `task_id`**, which is their idempotency key, and a **24-hex `hire_id`**. A run
  task's id is a slug (`laptop`) and a hire's id is `h_…`, so no agent could build a valid call.
- The laptop and account tools need the hire's **role** and **location** (profile, office). They are
  hire fields, not template inputs, and the agent had no way to learn them.
- The eval cases `tp-001`…`tp-003` expect the welcome message to name the hire and state the start date,
  and EVAL-1's scripted trajectories read `new_hires`. `templates_agent`'s manifest granted no read of
  `new_hires`, so a real model would be denied by the hub exactly where the trajectory reads.

## Decision

1. **Two opaque references in the input.** `onb_orchestrator.agent_input.opaque_ref(org, kind, *parts)` is
   a SHA-256 digest of the org, the kind and length-prefixed parts, cut to 24 hex characters.
   `task_key = opaque_ref(org, "task", run_id, task_id)` is the idempotency key: stable across attempts, so
   a retried task never creates a second account or order; different per run, org and task.
   `hire_key = opaque_ref(org, "hire", hire_id)` is the hire's id in a form the mocks accept. Both are
   derived, not stored, and are not secrets (an unkeyed digest of ids the agent already holds; an HMAC is the
   hardening if that ever matters). The agent passes them through.
2. **The hire's non-PII variables in the input.** `variables` is `onb_agents.runs.hire_variables(hire)`:
   `role`, `location`, `start_date`, `manager_id`, read tenant-scoped from `new_hires`, and nothing else
   of the hire (name, notes, tokens, masks never enter the input). A missing hire gives `{}` (the agent
   then has nothing to act on and answers `needs_human`); it does not fail the run engine's tests that
   have no hire records.
3. **`templates_agent` may read `new_hires`.** Its manifest read list becomes `[new_hires, run_tasks,
   templates]`, still no write tool. Hire documents hold only vault tokens for sensitive values (§7.3), and
   the recipient is still resolved only at the sink with a grant (§7.10). A test
   (`test_task_agent_manifests.py`) now checks every tool call and collection in the AGT-1 eval
   trajectories is inside the agent's manifest, so a manifest that contradicts its eval cases fails `make check`.
4. **Not changed.** `db_agent`'s write collections stay `run_tasks`, `documents_meta` (REG-1's stricter
   version, pending Haneef's PRD §7.3 decision); a test pins it. `provisioning_agent` is unchanged.

## Consequences

- A real model must copy `task_key` and `hire_key` into mock calls and derive `profile`/`office` from
  `variables`. The scripted model of the integration tests does exactly that; the real-connector eval
  run (needs OW-1) is what shows a real model does. Eval cases for provisioning (none exist yet) belong to
  that run.
- `agent_input` makes one extra tenant-scoped read per task.
- Templates that want the hire's name in a message depend on the templates agent's read of `new_hires`.
  A narrower projection (name and start date only) needs a hub-side field projection, which MCP-3 does not
  offer; left for SEC-1.
- The seed template's `bitbucket_access` carries `requires_approval: true`; the engine does not act on it
  until HIL-1. In the AGT-1 run it completes like any task; HIL-1 will turn it into an approval task.
