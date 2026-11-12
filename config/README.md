# config

Runtime configuration (PRD §11): `llm.yaml`; `agents/*.yaml` (one manifest per agent) and `tool_catalog.yaml`,
synced into the registry with `uv run python -m onb_agents.registry sync --actor <user id>`;
`pii_sink_policy.yaml`; `approval_sla.yaml`, when an undecided approval reminds its approver and escalates to HR
(read by the orchestrator worker; `APPROVAL_SLA_CONFIG` names another file; ADR 0027); `seed.yaml`, what `make seed` creates (the org, its users and the templates to
publish; passwords come from the `SEED_*_PASSWORD` variables it names, never from this file).
No credentials or model ids in code; they live here or in the environment.
