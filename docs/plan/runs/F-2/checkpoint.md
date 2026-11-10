# Checkpoint — F-2 · Run the base stack in Docker Compose

Branch `goal/f-2-compose-stack`. Session 1, 2026-11-10.

## Steps done
1. RED: `tools/tests/test_check_env_example.py`, `tools/tests/test_compose_static.py` (33 failing,
   `docs/evidence/F-2/red-static-tests.txt`); live tests `tools/tests_int/test_compose_stack.py` and
   `libs/py-data/tests_int/` written before the stack existed.
2. GREEN: `infra/docker-compose.yml`, mongo init/keyfile scripts, collector Dockerfile + config,
   `Caddyfile.dev`, `.env.example`, `scripts/check_env_example.py`, Makefile `up/down/logs/env-check`,
   `test-int: up`, py-data `test-int` target + pymongo.
3. Evidence for all five boxes in `docs/evidence/F-2/`; 11 mutation checks recorded.

## Next
Code and security review of `git diff main...HEAD`, handoff, goal-verifier, board to `in review`.

## Surprises
- Collector 0.165 deprecates the `otlp` exporter alias; it is `otlp_grpc` now.
- Jaeger v2 serves the query API at `/api/v3/services` (`/api/services` is 404).
- The rtk shell hook truncates `make` output into files; use `rtk proxy make …` for evidence.
