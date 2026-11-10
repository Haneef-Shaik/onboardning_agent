# Checkpoint — REG-1 · agent registry

Branch `goal/reg-1-agent-registry`.

## Steps done
1. `AgentManifest` model + validator (`libs/py-agents/src/onb_agents/registry/manifest.py`); box 1 tests in `test_manifest.py`.
2. Six manifests in `config/agents/`, `config/tool_catalog.yaml`; box 3 tests in `test_config_manifests.py`.
3. `AgentRegistry.sync` (version bump, audit per org, one transaction), `MongoRegistryStore`,
   `python -m onb_agents.registry sync`; box 2 tests in `test_sync.py`, live in `apps/api/tests_int/test_mongo_registry.py`.
4. `GET /agents`, `GET /agents/{name}` (HR_ADMIN), contracts regenerated, ROLE_MATRIX rows + `SAMPLE_PATHS`.
5. `make test-int` green on the ob-reg-1 stack (api: 25 passed, 7 of them registry).

## Next
Mutation checks, code/security review, `make check`, handoff, goal-verifier, board → in review.

## Surprises
- `rtk` truncates `make` output; run gates as `rtk proxy make …` to see every result line.
- The role-matrix test requested `/agents/{name}` literally; added `SAMPLE_PATHS` for path-parameter routes.
- The registry is global but the audit log is per org: each change is audited in every org in `orgs`.
