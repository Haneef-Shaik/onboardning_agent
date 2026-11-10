# Checkpoint — EVAL-1 · Build the deterministic eval harness

Branch `goal/eval-1-eval-harness` (from main b33ed1f). Compose project `ob-eval-1` (ports 19400/19401).

## Done
- Case loader with typed params for all 29 catalog checks (`cases.py`, `checks/params.py`).
- The 29 checks (`checks/`), interim output contracts (`contracts.py`), metrics and gates (`metrics.py`,
  `gates.py`). Box 1 table: `tests/test_check_catalog.py`. Box 3: `tests/test_metrics_gates.py`.
- Fixture loader through the real vault (`fixture.py`, `vault_seed.py`, `tokens.py`), ADR 0003 and the
  narrowed PII-2 boundary allowlist + `tests/test_harness_boundary.py`.
- Runner, toolbox (hub stand-in with audit), scripted agent on the `fake` connector, eval spans.

## Next
1. `databases.py` (throwaway Mongo DB per repetition, dropped after), `report.py` (evals/reports/*.json|md),
   `results.py` (`eval_runs` through TenantRepository), `cli.py` (`python -m onb_evals run|selftest`).
2. `evals/selftest/scripted_outputs_v1.jsonl` for all 27 cases (+ negative controls).
3. `make evals-selftest`, Nx project `evals` (`nx run evals:run`), `libs/py-evals` `test-int` target and
   `tests_int/` against compose Mongo (box 2 and box 4 evidence).
4. Mutation checks, reviews, gates, handoff, verifier.

## Surprises
- PII-2's static guard forbids `Vault` outside apps/api and py-crypto; resolved by ADR 0003.
- Seed tokens are not vault-shaped; `TokenMap` maps seed names ↔ issued tokens at the harness edges.
