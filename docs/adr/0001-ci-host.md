# 0001 · CI host: GitHub Actions

- **Status:** accepted · 2026-11-10 · goal F-3 · closes DEC-5
- **Context:** PRD §12 asks for a merge gate: lint, types, unit tests and ≥ 80% coverage on Python
  libs (`make check`), the cross-tenant suite "runs in CI and blocks merges" (§7.9), and §8.2 has CI
  run the eval self-test on every PR. The integration tests start Mongo with testcontainers, so the
  integration job needs a Docker daemon. There is one developer and no infrastructure team.
- **Numbering.** The card names this file `0001-ci-host.md`, and F-1 had already written
  `0001-nx-uv-monorepo-layout.md`. The path follows the card, because the plan and the ledger point
  at it. Renumbering is Haneef's call (see F-3's handoff).

## Decision

1. **CI runs on GitHub Actions** with GitHub-hosted `ubuntu-24.04` runners, from one workflow,
   `.github/workflows/ci.yml`.
2. **Two jobs, in order.** `check` runs `make bootstrap` then `make check`. `integration` needs
   `check` and runs `make bootstrap` then `make test-int`. CI runs the same Makefile targets as a
   developer does, so there is one definition of "green". Later gates (`make test-xt`,
   `make contracts-check`, `make evals-selftest`) are added to these jobs by the goals that create
   them (T-3, C-1, EVAL-1).
3. **Triggers.** Every push to any branch, and every pull request into `main`. Superseded runs on
   the same ref are cancelled.
4. **Caches.** uv's download cache (keyed on `uv.lock`), npm's cache (keyed on
   `package-lock.json`), and the Nx task cache in `.nx/cache` (keyed per job on both lock files
   plus the commit, restoring the newest earlier entry).
5. **Hardening.** The token is read-only (`permissions: contents: read`). Checkout doesn't keep it.
   Every action is pinned to a full commit SHA, with its tag in a comment. Both jobs have a
   timeout. The workflow uses no secrets.
6. **A red run blocks merging.** Branch protection on `main` requires the status checks `"check"`
   and `"integration"` to pass. Branch protection matches checks by job name, so renaming a job
   needs the rule updated too (guarded by `test_adr_names_the_required_status_checks_as_the_job_names`).

`tools/tests/test_ci_workflow.py`, which `make check` runs, guards every point above.

## Why

- **Docker on the runner.** Hosted Ubuntu runners come with a Docker daemon, so testcontainers works
  without docker-in-docker or a privileged service.
- **Nothing to run.** A solo developer shouldn't have to look after CI servers. Hosted runners are
  free for public repositories and come with a monthly allowance for private ones, which is enough
  for this MVP.
- **Merge gate built in.** Required status checks on a protected branch give "red blocks merge"
  with no extra tooling, and pull requests show the results.
- **The tools already fit.** The `gh` CLI is already part of the workflow (see the git rules).
  `setup-uv` and `setup-node` have native caching that matches the toolchain F-1 pinned
  (uv 0.11.x, Node from `.nvmrc`).

### Alternatives considered

| Option | Why not |
|---|---|
| GitLab CI | Means moving the code host or mirroring it. Docker-in-docker for testcontainers needs privileged runners. |
| Self-hosted runner on the deployment VM | It mixes CI with the production host (PRD §11) and needs patching and isolation work. |
| Jenkins / Buildkite | Needs a server or agents to run. That is too much overhead for one developer. |
| Local hooks only (`pre-push`) | They can be skipped, and they can't block a merge. |

## Consequences

- **No remote yet.** On 2026-11-10 the repository has no remote. The workflow is ready but hasn't
  run. F-3's handoff records this, and so does MS-0's "CI is green on `main`" check.
- **Turning it on** (Haneef's step, after creating the GitHub repository and pushing `main`):

  ```bash
  gh api -X PUT "repos/{owner}/{repo}/branches/main/protection" \
    --input - <<'JSON'
  {
    "required_status_checks": { "strict": true, "contexts": ["check", "integration"] },
    "enforce_admins": true,
    "required_pull_request_reviews": null,
    "restrictions": null
  }
  JSON
  ```

  Then prove the gate: push a branch with a deliberately failing test (expect `check` to go red,
  and `integration` to be skipped), fix it, and push again (expect both green). Record both run links.
- Moving off GitHub means rewriting `ci.yml`. The jobs only call `make`, so the rewrite is small.
