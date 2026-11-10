# Checkpoint — F-1

**Done:** git init on main (import commit 13eb884); layout guard tests (RED, then GREEN); 17 Nx
projects; root uv workspace; Makefile; workspace-local Nx cache; per-project tool caches; evidence
for all five boxes and nine mutation checks in `docs/evidence/F-1/`.

**Next:** code review of `git diff main...HEAD`, handoff, goal-verifier, close.

**Surprises:**
- Nx 24 caches in `~/.nx/<hash>` by default, so a "fresh clone" ran 100% from cache. Fixed with
  `cacheDirectory: ".nx/cache"`.
- On the first truly cold run, two parallel mypy tasks crashed sharing `.mypy_cache`. Fixed with
  per-project `--cache-dir`, pytest `cache_dir` and `COVERAGE_FILE`.
- TypeScript 6 rejects `baseUrl` (deprecated); `paths` are relative to `tsconfig.base.json`.
- The rtk hook rewrites `npx nx …` into `npm run nx`; use `node_modules/.bin/nx` or `rtk proxy`.
