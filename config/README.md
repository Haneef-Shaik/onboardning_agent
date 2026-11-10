# config

Runtime configuration (PRD §11): `llm.yaml`; `agents/*.yaml` (one manifest per agent) and `tool_catalog.yaml`,
synced into the registry with `uv run python -m onb_agents.registry sync --actor <user id>`;
`pii_sink_policy.yaml`.
No credentials or model ids in code; they live here or in the environment.
