# Checkpoint — MCP-3 · least-privilege DB users

Branch `goal/mcp-3-least-privilege-db-users` (from main @ 46f80e1).

## Steps done
1. `onb_mcp_mongo/db_users.py`: `READ_ONLY` / `READ_WRITE` specs, `role_privileges`, `provision`
   (idempotent create/update of roles and users in `admin`), `check_privileges`,
   `assert_least_privilege` (start-up check via `connectionStatus`), CLI `python -m onb_mcp_mongo.db_users`.
   Unit tests `tests/test_db_users.py` (RED: `docs/evidence/MCP-3/red-step1.txt`).
2. Wiring: `MCP_MONGO_URI` is the read-only user, new required `MCP_MONGO_WRITE_URI` the read-write
   user; `main` checks both users before serving; `build_server(..., write_store=)` routes
   `insert_one`/`update_one` (`server.WRITE_TOOLS`) to the write store. Tests `tests/test_main.py`,
   `tests/test_store_routing.py` (RED: `red-step2.txt`).
3. Integration `tests_int/test_db_users_int.py` against testcontainers Mongo (boxes 1 and 2):
   `box-tests.txt` 46 passed.
4. Box 3: `record_roles.py` → `db-getUser.txt`.
5. Mutation checks: `mutate.py` → `mutation-checks.txt`, 9 of 9 caught.

## Next
Reviews (code + security), `make check`, `make test-int`, handoff, goal-verifier, board → in review.

## Surprises
- mypy: a Protocol for `Database.command` must be `(*args: Any, **kwargs: Any)` to accept PyMongo's overloads.
- The worktree guard refuses compound shell commands that mention git or write files with heredocs.
