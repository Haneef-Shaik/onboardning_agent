# 0001 · CI host: GitHub Actions

- **Status:** accepted · 2026-11-10 · goal F-3 · closes DEC-5 · revised 2026-11-13 (three jobs, every Makefile gate)
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
2. **Three jobs; every Makefile gate runs once.** CI runs the same Makefile targets as a developer
   does, so there is one definition of "green":

   | Job | Needs | Runs |
   |---|---|---|
   | `check` | — | `make bootstrap`, runner `.env`, `make check` (plan, env, tooling, lint, typecheck, unit tests + coverage, and the cross-tenant suite `test-xt`), `make contracts-check` |
   | `integration` | `check` | `make bootstrap`, runner `.env`, `make test-int` (stack checks + every project's testcontainers/compose `test-int`), `make evals-selftest` (fake connector, no model calls) |
   | `pii` | `check` | `make bootstrap`, runner `.env`, `make test-pii`, then the reports and `make down` (`if: always()`); added by SEC-1 |

   The **runner `.env`** comes from `tools/ci_env.py` (SEC-1): `.env.example` with every empty value
   filled by a fresh random one and the `fake` LLM connector. It holds no real credential and lives
   only for that job. A new gate is added to a job here, or to a Makefile target a job already calls
   (`test_every_makefile_gate_runs_in_ci_exactly_once` fails until it is).
3. **Triggers.** Every push to any branch (the card says every push), and every pull request into
   `main`. A newer push cancels the older run on the same branch, except on `main`: every commit
   that lands there gets its own verdict. No job and no gate step has an `if:`; nothing has
   `continue-on-error`. Only report and clean-up steps may carry `if: always()`.
4. **Caches.** uv's download cache (keyed on `uv.lock`), npm's cache (keyed on
   `package-lock.json`), and the Nx task cache in `.nx/cache` (keyed per job on both lock files
   plus the commit, restoring the newest earlier entry).
5. **Hardening.** The token is read-only (`permissions: contents: read`). Checkout doesn't keep it.
   Every action is pinned to a full commit SHA, with its tag in a comment. Every job has a
   timeout. The workflow uses no repository secrets.
6. **A red run blocks merging.** Branch protection on `main` requires the status checks `"check"`,
   `"integration"` and `"pii"` to pass. Branch protection matches checks by job name, so renaming a
   job needs the rule updated too (guarded by `test_adr_names_the_required_status_checks_as_the_job_names`).

`tools/tests/test_ci_workflow.py` (and SEC-1's `test_ci_pii.py`), which `make check` runs, guard
every point above.

## Why

- **Docker on the runner.** Hosted Ubuntu runners come with a Docker daemon, so testcontainers works
  without docker-in-docker or a privileged service.
- **Nothing to run.** A solo developer shouldn't have to look after CI servers. Hosted runners are
  free for public repositories and come with a monthly allowance for private ones, which is enough
  for this MVP.
- **Merge gate built in.** Required status checks on a protected branch give "red blocks merge"
  with no extra tooling, and pull requests show the results.
- **The toolchain fits.** `setup-uv` and `setup-node` have native caching that matches the
  toolchain F-1 pinned (uv 0.11.x, Node from `.nvmrc`).

### Alternatives considered

| Option | Why not |
|---|---|
| GitLab CI | Means moving the code host or mirroring it. Docker-in-docker for testcontainers needs privileged runners. |
| Self-hosted runner on the deployment VM | It mixes CI with the production host (PRD §11) and needs patching and isolation work. |
| Jenkins / Buildkite | Needs a server or agents to run. That is too much overhead for one developer. |
| Local hooks only (`pre-push`) | They can be skipped, and they can't block a merge. |

## Consequences

- **Remote.** Since 2026-11-13, `origin` is `github.com/Haneef-Shaik/onboardning_agent`, and `main`
  is pushed. Nothing in this repository pushes or changes GitHub settings. These steps are Haneef's.
- **Turning on the gate** (once, after a run has reported all three checks, so GitHub knows their
  names). Branch protection on a private repository needs a paid plan (Pro/Team). On a free
  private repository, make it public or use a plan that allows it.

  ```bash
  gh api -X PUT "repos/Haneef-Shaik/onboardning_agent/branches/main/protection" \
    --input - <<'JSON'
  {
    "required_status_checks": { "strict": true, "contexts": ["check", "integration", "pii"] },
    "enforce_admins": true,
    "required_pull_request_reviews": null,
    "restrictions": null
  }
  JSON
  ```

  With `enforce_admins`, even Haneef can't push a commit to `main` until its checks pass. The local
  flow still works: push the goal branch, wait for green, fast-forward `main` locally and push it.
  The commit is the same, so its checks are already green. With `strict`, the branch must be up to
  date with `main` first.
- **Proving the gate** (F-3 boxes 2 and 3): push a branch with a deliberately failing unit test.
  `check` should go red, and `integration` and `pii` are skipped. Remove the test and push again:
  all three should go green. The `integration` log shows testcontainers starting Mongo
  (`test-int` targets of `py-data`, `api`, …). Record the run links in F-3's handoff.
- **Run time.** Each job builds and starts the compose stack on its own runner. `check` is the
  longest gate, because it contains `test-xt`. The timeouts are 30, 40 and 30 minutes.
- **Same-repo pull requests run twice,** once for the push and once for the pull request. This is
  accepted: one developer, few PRs, and fork PRs still get a run.
- Moving off GitHub means rewriting `ci.yml`. The jobs only call `make`, so the rewrite is small.
