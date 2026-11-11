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
- `make contracts`, then the mutation checks (`docs/evidence/TPL-2/mutate.py`) and the reviews.
- Gates: `make check`, `make test-int`, `make test-xt`, `make contracts-check`.
- Write the handoff, run the verifier, set the board to in review, then `make down`.

## Surprises
- Worktree guard: put helper scripts in the job tmp folder; don't source `.env` inline (use `tmp/withenv.sh`).
- An async Mongo client is bound to one event loop: seed through a separate store.
