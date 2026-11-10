# 0001 · Nx + uv monorepo layout and naming

- **Status:** accepted · 2026-11-10 · goal F-1
- **Context:** PRD §11 fixes Nx with `@nxlv/python` + uv, Python 3.12, and the 17-project layout.
  The card's trap: `@nxlv/python` defaults to Poetry and can give each lib its own virtualenv.

## Decision

1. **One root uv workspace.** The root `pyproject.toml` (`[tool.uv] package = false`) lists every
   Python app and lib as a workspace member and depends on all of them, so `uv sync` installs each
   one editable into the single `.venv` at the root. There is one `uv.lock`. Projects import each
   other by package name with no publishing. Members never get their own `.venv` or lock file
   (guarded by `tools/tests/test_workspace_layout.py`).
2. **`@nxlv/python` is registered with `packageManager: "uv"`** in `nx.json`, and Python targets use
   its `run-commands` executor with `uv run --frozen …` from the workspace root.
3. **Names.** The Nx project name equals the folder name (`py-data`, `mcp-hub`, `web`). Python
   distributions are `onb-<name>` and import packages are `onb_<name>`, with the `py-` prefix dropped
   (`libs/py-data` → `onb_data`, `apps/mcp-hub` → `onb_mcp_hub`). Each uses a `src/` layout with a
   `py.typed` marker. TypeScript libs are imported as `@onb/ts-ui` and `@onb/ts-api-client`
   (paths in `tsconfig.base.json`).
4. **Targets.** Every project has `lint`, `typecheck`, `test`. Python: ruff (check + format),
   mypy `strict = true` (root config), pytest. Web: eslint (typescript-eslint strict), `tsc`, Vitest.
5. **Coverage.** Each `libs/py-*` `test` target runs `--cov=onb_<name> --cov-fail-under=80`
   (line coverage; `branch = false`). Apps are not held to the threshold (PRD §12 names Python libs).
6. **`make check`** = `plan.py check` → repo guard tests (`tools/tests`, `scripts/plan/test_plan.py`)
   → `nx run-many -t lint`, `typecheck`, `test`. Repo guard tests are not an Nx project, so the project
   count stays at the PRD's 17.

## Consequences

- Adding a Python project means: folder + `pyproject.toml` + `project.json`, a root workspace member
  and a root dependency/source entry. The layout test fails until all of them exist.
- Third-party Python dependencies go in the member's own `pyproject.toml`; `uv lock` updates the one
  root lock.
