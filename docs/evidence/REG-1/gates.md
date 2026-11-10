# REG-1 gate results

Branch `goal/reg-1-agent-registry` at `ff394c9` (code complete). macOS, Docker compose project `ob-reg-1`
(ports 18100/18101), 2026-11-10. Gates run with `rtk proxy make …` so no output is truncated.

| Gate | Command | Result line |
|------|---------|-------------|
| Check | `make check` | `NX Successfully ran target lint for 17 projects` · `… typecheck for 17 projects` · `… test for 17 projects` · `make check: all passed` (py-agents coverage `TOTAL 556 14 97%`) |
| Integration | `make test-int` | `tools/tests_int 12 passed`; `nx run api:test-int 25 passed` (7 in `test_mongo_registry.py`); py-data 28, mcp-mongo 77, py-eventbus 17, py-crypto 10, py-telemetry 3, py-evals 2; `NX Successfully ran target test-int for 7 projects` |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Eval self-test | `make evals-selftest` | `result: PASS` · `eval_runs: evr_20261110T184744Z_e6973eb3` · `1 passed in 48.80s` |
| Cross-tenant | `make test-xt` | not available yet (T-3 not on main); cross-tenant case in `apps/api/tests/test_agents_route.py::test_both_orgs_admins_see_the_same_global_registry_and_no_org_data` |

## Box tests

| Box | Command | Result |
|-----|---------|--------|
| 1 | `uv run --frozen pytest libs/py-agents/tests -q -k "vault_collection or vault_name or yaml_binary"` | `20 passed, 95 deselected` |
| 2 | `uv run --frozen pytest libs/py-agents/tests -q -k bumps_the_version` | `1 passed, 114 deselected` (live: `apps/api/tests_int/test_mongo_registry.py::test_changing_a_yaml_and_resyncing_bumps_the_version_and_audits_it_in_mongo`, in the api:test-int 25 passed) |
| 3 | `uv run --frozen pytest libs/py-agents/tests -q -k "six_manifests_validate or matches_the_eval_fixture"` | `2 passed, 113 deselected` |
| 4 | `uv run --frozen pytest -q apps/api/tests/test_agents_route.py apps/api/tests/test_role_matrix.py -k agents` | `26 passed, 26 deselected` |
