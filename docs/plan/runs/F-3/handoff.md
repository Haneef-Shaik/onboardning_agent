# Handoff — F-3 · Gate every change in CI        attempt 2 · goal/f-3-ci-gate · rebased on main 912ad50 · code at c077695

**Outcome claimed.** `.github/workflows/ci.yml` runs every Makefile gate in CI, on every push and
every PR into `main`:
- `check`: `make check`, which includes `test-xt`, then `make contracts-check`.
- `integration`: `make test-int` (testcontainers plus the compose stack), then `make evals-selftest`.
- `pii`: SEC-1's `make test-pii`.

Each job writes a runner `.env` with `tools/ci_env.py` before it brings the stack up. The ADR names
`check`, `integration` and `pii` as the required checks that block merging into `main`.

A local rehearsal of the exact job commands is green, and a testcontainers Mongo was seen starting.
**Boxes 2 and 3 still need hosted run links.** `origin` exists now, but this session must not push
or change GitHub settings, so those runs are Haneef's steps (below).

## What changed since attempt 1
- **Rebased onto local main 912ad50** (authorised). Main already had `ci.yml`: SEC-1 copied F-3's
  workflow and added the `pii` job. The conflict was resolved to main's version, and
  `pyproject.toml`/`uv.lock` to main's too. `pyyaml` is declared again in the dev group, because
  main kept only `types-pyyaml`.
- **The workflow on main was broken for a fresh runner.** Main's `make check` runs `test-xt`, and
  `make test-int` brings the compose stack up. Both depend on `.env` (`$(ENV_FILE)` exits 1 with
  "No .env"). Only `pii` generated one. Now `check` and `integration` run
  `uv run --frozen python tools/ci_env.py > .env` right after bootstrap.
- **The current gates run in CI:** `contracts-check` (in `check`) and `evals-selftest` (in
  `integration`). `test-xt` runs inside `make check`, and `test-pii` in `pii` (kept unchanged).
- **Timeouts** are `check` 30, `integration` 40 and `pii` 30 minutes. Locally `make check` took
  623 s, of which `test-xt` was 521 s.
- **Guards** cover all three jobs (38 pass, with SEC-1's `test_ci_pii.py`). New ones:
  - `test_every_makefile_gate_runs_in_ci_exactly_once`
  - `test_the_runner_env_is_written_after_bootstrap_and_before_any_gate[*]`
  - `test_make_check_includes_the_cross_tenant_suite`
  - `test_no_job_or_gate_step_can_be_skipped_or_allowed_to_fail[*]`: only non-gate report and
    clean-up steps may use `if: always()`, and nothing may use `continue-on-error`.
- **The ADR is revised**: three jobs, the runner `.env`, the remote, the protection payload with
  `pii`, the plan caveat and the run times.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `docs/adr/0009-ci-host.md` records the host and why (closes DEC-5) | yes | The ADR: GitHub Actions; Decision (jobs table, runner `.env`, triggers, caches, hardening, required checks), Why, Alternatives, Consequences. `uv run --frozen pytest tools/tests/test_ci_workflow.py tools/tests/test_ci_pii.py -q` gives `38 passed`. |
| A CI run with a deliberately failing test is red, and the fixed run is green (both run links in the handoff) | **no: needs Haneef to push** | There are no hosted runs yet (`origin/main` 4493565 has no workflow). Local stand-ins: `docs/evidence/F-3/local-red-green.txt` (attempt 1: red exit=2, then green exit=0), and `docs/evidence/F-3/attempt2-local-ci.txt` at c077695, where every `check` and `integration` command exits 0. The demo is step 3 below. |
| The integration job starts a testcontainers Mongo and passes (run link) | **no: needs Haneef to push** | No run link yet. Local proof, `docs/evidence/F-3/attempt2-testcontainers.txt`: during `nx run mcp-mongo:test-int` (`79 passed`, exit=0), Docker started `testcontainers/ryuk:0.8.1` and `mongo:8.0.34@sha256:b992…` with label `org.testcontainers=true`. The full `make test-int` gives `Successfully ran target test-int for 10 projects`, exit=0. |

## What Haneef must do (in order)
This session pushed nothing, opened no PR and changed no GitHub setting.

1. **Integrate F-3 before pushing `main` again.** Local `main` (912ad50) has a `ci.yml` whose
   `check` and `integration` jobs stop at "No .env" on a runner. If `main` is pushed before this
   branch lands, the first runs on `main` are red for that reason, not because of a real failure.
2. **Push the branch.** CI runs on every push:
   `git push -u origin goal/f-3-ci-gate`
   Wait for `check`, `integration` and `pii` to go green (expect about 15–25 minutes in total).
   That run is the **green run link** for box 2 and the **integration run link** for box 3. In
   its `integration` log, `make test-int` shows `mcp-mongo` / `mcp-hub` testcontainers starting
   `mongo:8.0.34`.
3. **Red/green demo** (box 2), on a throwaway branch so the goal branch stays clean:
   ```bash
   git switch -c ci-demo/red goal/f-3-ci-gate
   printf 'def test_deliberate_failure() -> None:\n    assert 1 + 1 == 3, "F-3: deliberately failing test"\n' \
     > tools/tests/test_zz_deliberate_failure.py
   git add tools/tests/test_zz_deliberate_failure.py
   git commit -m "test(F-3): deliberately failing test (CI demo)"
   git push -u origin ci-demo/red     # expect: check red at tooling-test; integration and pii skipped → RED run link
   git revert --no-edit HEAD
   git push                           # expect: check, integration, pii all green → FIXED run link
   git switch goal/f-3-ci-gate
   git push origin --delete ci-demo/red && git branch -D ci-demo/red   # clean up the demo branch
   ```
4. **Branch protection.** Do this after step 2, once GitHub has seen the three check names:
   ```bash
   gh api -X PUT "repos/Haneef-Shaik/onboardning_agent/branches/main/protection" --input - <<'JSON'
   {
     "required_status_checks": { "strict": true, "contexts": ["check", "integration", "pii"] },
     "enforce_admins": true,
     "required_pull_request_reviews": null,
     "restrictions": null
   }
   JSON
   ```
   - Branch protection on a **private** repository needs GitHub Pro/Team. On a free private
     repository the call returns 403: make the repository public or change the plan.
   - With `enforce_admins`, a push to `main` is accepted only once that commit's checks are green.
     The local flow (push branch → green → `git merge --ff-only` → push `main`) still works,
     because the commit is the same.
   - To check: `gh api repos/Haneef-Shaik/onboardning_agent/branches/main/protection --jq '.required_status_checks.contexts'`
     should print `["check","integration","pii"]`.
5. **Record and re-verify.** Paste the three links (red, fixed and green, and the integration run)
   into this handoff's Done-when rows, set the box rows to yes, and re-run `goal-verifier` (attempt 3).

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| CI = `check` (`make check` incl. `test-xt`, `make contracts-check`) → `integration` (`make test-int`, `make evals-selftest`) and `pii` (`make test-pii`) in parallel. | `test_check_job_…`, `test_integration_job_runs_after_check_with_the_stack_gates`, `test_ci_pii.py` |
| A new Makefile gate isn't covered until it runs in a job: `test_every_makefile_gate_runs_in_ci_exactly_once` lists them (`CI_GATES`). Add the step and the name together. | that test; mutations M16, M17, M21 |
| Every job that needs the stack writes `.env` with `tools/ci_env.py` after bootstrap, before any gate. Add a new required variable to `.env.example`, and `ci_env.py` fills it. | `test_the_runner_env_is_written_after_bootstrap_and_before_any_gate[*]`, SEC-1's `test_the_runner_env_fills_every_variable_with_fresh_values` |
| Required checks are `check`, `integration` and `pii`, equal to the job names. A rename needs the ADR payload and the GitHub rule changed together. | `test_adr_names_the_required_status_checks_as_the_job_names` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| DEC-5 @ MS-0 | dev machine (decision record), 2026-11-10 | pass | `docs/adr/0009-ci-host.md` | yes, in attempt 1. Kept after the rebase; `plan.py check` is ok inside `make check` |

## Gates (at c077695, local main 912ad50 + F-3)
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (runner-style `.env`, compose `ob-f-3`) | exit=0, 623 s; tooling `255 passed, 67 subtests passed`; lint/typecheck/test 17 projects; `test-xt` `362 passed` + `mcp-hub` test-xt; `make check: all passed` |
| Contracts | `make contracts-check` | exit=0 |
| Integration | `make test-int` | exit=0; `12 passed` (stack checks), `Successfully ran target test-int for 10 projects` |
| Eval self-test | `make evals-selftest` | exit=0; `1 passed` |
| PII | `make test-pii` | not re-run here; SEC-1's job is unchanged |
| Workflow lint | `uvx --from actionlint-py actionlint .github/workflows/ci.yml` | exit=0 |

The full transcript is in `docs/evidence/F-3/attempt2-local-ci.txt`. The stack was torn down
(`make down` exit=0), and `.env` was removed afterwards.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| SEC-1 | `tools/ci_env.py` prints a complete runner `.env` with fresh secrets and the fake connector | yes: used for the local rehearsal (project and ports overridden), all gates green |
| F-2 / T-3 / C-1 / EVAL-1 | `test-int`, `test-xt`, `contracts-check`, `evals-selftest` Makefile targets | yes: all exit 0 locally |

## Mutation checks
Full output: `docs/evidence/F-3/mutation-checks.txt` (script `docs/evidence/F-3/mutate.py`; each
file is restored, `restored: True`). All 22 were caught (pytest exit=1):
- M1–M14 as in attempt 1. Payload M8 is now `[check, pii]`.
- M15: `check` no longer writes `.env` → `test_check_job_…` and `test_the_runner_env_…[check]` fail.
- M16: `contracts-check` dropped → `test_every_makefile_gate_runs_in_ci_exactly_once` fails.
- M17: `evals-selftest` dropped → the same test fails.
- M18: `.env` written after `test-int` → `test_the_runner_env_…[integration]` fails.
- M19: `if: always()` on `make test-int` → `test_no_job_or_gate_step_…[integration]` fails.
- M20: ADR payload drops `pii` → the ADR test fails.
- M21: `make check` loses `test-xt` → `test_make_check_includes_the_cross_tenant_suite` fails.
- M22: `pii` job renamed away → `test_workflow_has_exactly_the_check_integration_and_pii_jobs` fails.

The RED run for the attempt-2 guards (6 failed before the workflow change) is in
`docs/evidence/F-3/attempt2-red.txt`.

## Review findings left open
From attempt 1 (sonnet review, no critical or high findings):
- medium: same-repo PRs run twice (push + pull_request). Accepted in the ADR.
- low: the uv version test accepts any 0.11.x in range.
- low: legacy `contexts` field in the protection payload.
- unverified until the first hosted run: the action SHAs match their tag comments.

## Left undone, and why
- **Boxes 2 and 3**: hosted runs need a push, which is Haneef's call (steps above).
- **`docs/plan/board.html` rebuild**: left to the orchestrator, because the file is shared.
- **ADR number clash**: `0009-ci-host.md` sits next to F-1's `0001-nx-uv-monorepo-layout.md`. I kept
  the card's path; renumbering is Haneef's call.

## Found for other goals
- **Orchestrator:** don't push local `main` (912ad50) before F-3 is integrated (step 1).
- **Cost:** each job brings up its own compose stack, and `check` alone takes about 10 minutes
  because of `test-xt`. If minutes become a concern, move `test-xt` out of `make check` into its
  own job. That is a Makefile/PRD §7.9 decision, not F-3's.

## Decisions forced (ADRs written)
- `docs/adr/0009-ci-host.md` revised 2026-11-13: three required checks, runner `.env`, every gate in CI.

## Traps hit
- Main's `ci.yml` came from SEC-1 copying this branch's workflow, so the rebase hit an add/add
  conflict, and main's guard test file wasn't there.
- `make check` on main needs a `.env` and Docker, because it runs `test-xt`. A fresh runner fails
  at `$(ENV_FILE)` unless `.env` is generated first.
- The rtk hook compresses `make` and `git` output; use `rtk proxy …` for real result lines.
