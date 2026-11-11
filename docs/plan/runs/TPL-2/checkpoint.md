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
- Done through gates; handoff written (attempt 1). Next: goal-verifier, then board `in review`, `make down`.
- Reviews fixed (security H1 body/inputs caps; code-review L5/L7/L8, M1/M2); mutations 32/32.

## Surprises
- Worktree guard: helper scripts go in the job tmp folder; no inline `.env` sourcing (use tmp/withenv.sh).
- Async Mongo client is loop-bound: seed through a separate store.
