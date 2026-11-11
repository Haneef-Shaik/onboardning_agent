# Checkpoint — TPL-2 · template lifecycle

Branch `goal/tpl-2-template-lifecycle`. Compose project `ob-tpl-2` (Caddy 18800/18801).

## Done
1. Tried making `template_versions` append-only in `onb_data` (f9d48be), then reverted it (9a3d976). It
   would break the Template Builder's MCP reads (mcp-mongo reads with `store.tenant`, and manifests
   refuse append-only collections). Immutability instead comes from: no update or delete path in
   `TemplateStore`, a unique `(org_id, key, version)` index, and insert-only versions (`VersionExistsError`).
2. Contracts: `onb_contracts.template_lifecycle`.
3. `onb_api.templates`: store (Mongo + in-memory), lifecycle, validator source, routes. Publish runs
   in one transaction with its audit record.
4. Role matrix rows, sample paths and sample bodies. `main.py` requires `CONFIG_DIR`.
5. test-int `apps/api/tests_int/test_mongo_templates.py` (5 passing).
6. test-xt: 9 new `ROUTE_CASES` rows; the xt world seeds template drafts and versions for both orgs.
   148 passed.

## Next
- Attempt 1: verifier pass at 667b563.
- Attempt 2: rebased onto main at 397df21 (HIRE-1 + MOCK-1), keeping both sides. Gates re-run green (check with
  test-xt 196, test-int, contracts-check, evals-selftest) and mutations still 32/32.
- Next: the attempt-2 verifier, then the board to `in review`, then `make down`.

## Surprises
- Worktree guard: helper scripts go in the job tmp folder; don't source `.env` inline (use tmp/withenv.sh).
- An async Mongo client is bound to one event loop: seed through a separate store.
