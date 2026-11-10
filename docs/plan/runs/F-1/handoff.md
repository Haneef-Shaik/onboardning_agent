# Handoff — F-1 · Create the Nx monorepo with every Python and web project        attempt 1 · goal/f-1-nx-monorepo · see `git log -1` (evidence captured at bc7a30b)

**Outcome claimed.** One Nx 24 workspace in which all 17 projects of the PRD §11 layout exist, each
with lint, typecheck and test targets. Python projects share one root uv workspace on 3.12 through
`@nxlv/python` pinned to uv. `make check` runs the plan check, the repo guard tests, and lint,
typecheck and test for every project, with an 80% line-coverage gate on each `libs/py-*`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| From a fresh clone, `make bootstrap && make check` exits 0 (command and last lines in the handoff) | yes | `docs/evidence/F-1/fresh-clone-check.txt`. This was a `git clone` of the branch at bc7a30b into a scratch dir. It ran cold: `Cache: 0/17 hit (0%)` for lint, typecheck and test. Last lines below. |
| `npx nx show projects` lists all 17 projects of the PRD §11 layout | yes | `docs/evidence/F-1/nx-show-projects.txt` shows 17 names, and the `--json` count prints `17`. Also proved by `test_layout_has_exactly_the_17_prd_projects`. |
| An 80% line-coverage threshold is enforced for every `libs/py-*` project, seen to fail once when a lib drops below it (output recorded) | yes | Each `libs/py-*/project.json` test target has `--cov=onb_<name> --cov-fail-under=80` with `branch = false`. Guarded by `test_python_lib_enforces_80_percent_line_coverage[*]` (9 cases) and `test_coverage_is_line_coverage`. The failure is in `docs/evidence/F-1/coverage-threshold-fails.txt`: an untested function was added to py-crypto, giving `FAIL Required test coverage of 80% not reached. Total coverage: 33.33%`, and both `nx run py-crypto:test` and `make test` exit non-zero. |
| `make check` runs `python3 scripts/plan/plan.py check` and fails when GOALS.md has a broken need (seen once) | yes | `Makefile`: `check: plan-check …` and `plan-check:` runs `python3 scripts/plan/plan.py check`. `docs/evidence/F-1/plan-check-broken-need.txt` has `Needs: F-1, ZZ-99`, which gives `plan error: F-2: needs 'ZZ-99', which is not a goal` and `make: *** [plan-check] Error 1` with exit=2. Guarded by `test_makefile_check_runs_plan_check_and_all_targets`. |
| `git check-ignore .env evals/reports/x.json` prints both paths | yes | `docs/evidence/F-1/check-ignore.txt` prints `.env`, then `evals/reports/x.json`, with exit=0. Also proved by `test_gitignore_covers[*]`. |

Last lines of the fresh-clone run:
```
 NX   Successfully ran target test for 17 projects
  Cache:             0/17 hit (0%)
make check: all passed
exit=0
```

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Every Python project is a uv workspace member installed editable into the single root `.venv`. Libs import each other by package name: `onb_<folder minus py->`, e.g. `onb_data`, `onb_mcp_hub`. | `test_root_uv_workspace_covers_every_python_project`, ADR 0001 |
| A new third-party Python dependency goes in that member's `pyproject.toml`, then `uv lock`. Workspace deps need `{ workspace = true }` in `[tool.uv.sources]`. | ADR 0001 |
| `make check` = plan check → ruff/mypy/pytest on `tools/` → `nx run-many -t lint/typecheck/test` | `Makefile`, `test_makefile_check_runs_plan_check_and_all_targets` |
| `make test-int` runs `nx run-many -t test-int`. No project has that target yet, so it exits 0 with "No tasks were run". | `Makefile` (F-2 adds `test-int` targets) |
| The Nx cache lives in `.nx/cache`, and typecheck/test cache keys include dependencies (`^default`) | `test_nx_cache_lives_in_the_workspace`, `test_cache_key_includes_dependencies[*]` |
| Python targets run with per-project mypy cache, pytest cache and `COVERAGE_FILE` (safe in parallel) | `test_python_targets_do_not_share_caches[*]` |
| TS libs resolve as `@onb/ts-ui` and `@onb/ts-api-client` (tsconfig paths, vitest aliases in web) | `apps/web` typecheck and test import `@onb/ts-ui` |
| `.gitignore` covers `.env`, `.env.*` (keeps `.env.example`), `.envrc`, `evals/reports/`, `.venv`, `node_modules`, `dist`, `build`, Mongo keyfiles, `*.pem`, `*.key`, `secrets/` | `test_gitignore_covers[*]`, `test_gitignore_keeps[*]` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-QUAL @ MS-6 | dev machine (macOS), 2026-11-10 | contributes: the 80% lib coverage gate exists | `coverage-threshold-fails.txt` | no (F-1 is not the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make bootstrap && make check` (fresh clone @ bc7a30b) | `make check: all passed`, exit=0, 0% cache |
| Check | `make check` (worktree) | exit 0. Tooling: `113 passed, 67 subtests passed`, and 17/17 for lint, typecheck and test |
| Integration | `make test-int` | No tasks yet (F-2 creates the gate) |

The other gates do not exist yet (T-3, C-1, EVAL-1, SEC-1, E2E-1).

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| — (no needs) | `scripts/plan/plan.py check` validates needs | yes: it rejects an unknown need (evidence above) |

## Mutation checks
Full output: `docs/evidence/F-1/mutation-checks.txt`. Each mutation ran in a scratch clone and was restored with "0 files differ".
- M1: `--cov-fail-under` removed from py-data. `test_python_lib_enforces_80_percent_line_coverage[libs/py-data]` FAILED.
- M2: threshold lowered to 70 on py-llm. `…[libs/py-llm]` FAILED.
- M3: `.env` removed from .gitignore. `test_gitignore_covers[.env]` FAILED.
- M4: `evals/reports/` removed. `test_gitignore_covers[evals/reports/x.json]` FAILED.
- M5: plan check replaced by `true` in the Makefile. `test_makefile_check_runs_plan_check_and_all_targets` FAILED.
- M6: `@nxlv/python` set to poetry. `test_nx_python_plugin_is_pinned_to_uv` FAILED.
- M7: shared mypy cache for py-eventbus. `test_python_targets_do_not_share_caches[libs/py-eventbus]` FAILED.
- M8: `cacheDirectory` removed. `test_nx_cache_lives_in_the_workspace` FAILED.
- M9: `apps/mcp-mocks/project.json` deleted. `test_layout_has_exactly_the_17_prd_projects` (and 2 more) FAILED.
- M10: `^default` dropped from cache inputs. `test_cache_key_includes_dependencies[typecheck|test]` FAILED.
- M11: `plan-check` dropped from `check:` prerequisites. `test_makefile_check_runs_plan_check_and_all_targets` FAILED.
- M12: coverage switched to branch. `test_coverage_is_line_coverage` FAILED.
- M13: `*.pem` removed. `test_gitignore_covers[infra/tls/server.pem]` FAILED.
- Product guards: the 80% threshold (`coverage-threshold-fails.txt`) and the broken-need plan check (`plan-check-broken-need.txt`) were both seen to fail.

RED before GREEN: `docs/evidence/F-1/red-layout-tests.txt` shows 72 failed before scaffolding. The
review-driven guards were also seen to fail (8 failed) before their fixes.

## Review findings left open
The code-reviewer (a general-purpose agent with a reviewer brief) reported no critical or high findings. It found 2 medium and 6 low. All are fixed except:
- low · `nx.json` sharedGlobals: the reviewer's note on the root `pyproject.toml`'s effect on tool versions is covered by `uv.lock` plus `.python-version`/`.nvmrc`, which are now in sharedGlobals. Nothing is open.
- Security review was not run: the diff touches no auth, tenancy, PII, vault, grants, MCP hub, guardrails or routes.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. This session worked to the same condition text (`plan.py goal F-1`).
- Web lint uses typescript-eslint strict only, with no React-hooks plugin. Add it when real components land (web goals).

## Found for other goals
- **EVAL-1 / MASTER-PLAN §7:** the eval gate is written `nx run evals:run …`, but the PRD §11 layout has no `evals` project. The harness project is `py-evals`. EVAL-1 should add a `run` target to `py-evals` and update the command to `nx run py-evals:run`, or justify an 18th project. The box-2 count of 17 would then change.
- **F-2:** add `test-int` targets to the projects (pytest + testcontainers). `make test-int` already fans out to them. Add `.env.example`; it is not ignored.
- **All Python goals:** a lib's real dependencies go in its own `pyproject.toml`. Workspace deps are `onb-<name>` with `{ workspace = true }` in the member's `[tool.uv.sources]`.
- **Tooling note:** the local rtk shell hook rewrites `npx nx …` to `npm run nx …`, which fails. Use `node_modules/.bin/nx` (or `rtk proxy npx nx`). The Makefile already uses `node_modules/.bin/nx`.

## Decisions forced (ADRs written)
- `docs/adr/0001-nx-uv-monorepo-layout.md` covers the one root uv workspace, `@nxlv/python` with uv, project and package naming, targets, line coverage on libs only, and guard tests kept outside Nx.

## Traps hit
- Nx 24's default cache is machine-global (`~/.nx/<hash>`). The first "fresh clone" run was 100% cache hits and proved nothing. Fixed by moving the cache to `.nx/cache`.
- On the first truly cold run, two parallel mypy tasks crashed with `INTERNAL ERROR` because they shared `.mypy_cache`. Fixed with per-project caches.
- TypeScript 6 errors on `baseUrl` (TS5101). `paths` are now relative to `tsconfig.base.json`.
- The card's trap (Poetry default, per-lib venv) was avoided. It is guarded by M6 and `test_python_project_has_no_own_virtualenv_or_poetry`.
