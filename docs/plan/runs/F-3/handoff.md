# Handoff — F-3 · Gate every change in CI        attempt 1 · goal/f-3-ci-gate · see `git log -1` (code at 736bd73)

**Outcome claimed.** The CI gate is **ready but has not run**. `.github/workflows/ci.yml` runs
`make bootstrap && make check`, then `make bootstrap && make test-int`, on every push and every PR
into `main`. ADR `0001-ci-host.md` chooses GitHub Actions and gives the branch-protection rule that
makes a red run block merging. **The repository has no git remote**, and this session must not push
(orchestrator rule, MASTER-PLAN §9.6). The card's scope covers this case: "the goal stops at
'workflow ready' if no remote exists yet and says so". So boxes 2 and 3 are **not met**. They need
hosted run links, which only exist after Haneef creates the remote and pushes.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `docs/adr/0001-ci-host.md` records the host and why (closes DEC-5) | yes | `docs/adr/0001-ci-host.md`: Status accepted, closes DEC-5, GitHub Actions; Decision, Why, Alternatives considered, Consequences. Guarded by `test_adr_records_github_actions_as_the_ci_host_and_closes_dec_5` and `test_adr_names_the_required_status_checks_as_the_job_names`, run with `uv run --frozen pytest tools/tests/test_ci_workflow.py -q`, result `22 passed`. |
| A CI run with a deliberately failing test is red, and the fixed run is green (both run links in the handoff) | **no: no remote** | There are no run links, because there is no remote to run on. Local stand-in only: `docs/evidence/F-3/local-red-green.txt` runs the check job's command at HEAD 736bd73. With `tools/tests/test_zz_deliberate_failure.py` it gives `FAILED …::test_deliberate_failure`, `make: *** [tooling-test] Error 1`, exit=2. With that file removed it gives `make check: all passed`, exit=0. `actionlint` 1.7.12 is clean (`docs/evidence/F-3/actionlint.txt`, exit=0). |
| The integration job starts a testcontainers Mongo and passes (run link) | **no: no remote, and no `test-int` target yet** | The integration job runs `make test-int` on `ubuntu-24.04`, which has a Docker daemon. On main today `make test-int` gives `NX   No tasks were run`, exit 0, because F-2 (running in parallel) adds the testcontainers targets. The local Docker daemon isn't running either. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| CI = two jobs, `check` (`make bootstrap`, `make check`) then `integration` (`needs: check`; `make bootstrap`, `make test-int`). To add a gate to CI, add it to the Makefile target that job runs, or add a `run:` line and update `tools/tests/test_ci_workflow.py`. | `test_check_job_bootstraps_then_runs_make_check`, `test_integration_job_runs_after_check_and_runs_make_test_int` |
| Branch protection must require the status checks `check` and `integration`, which equal the job display names. The ready `gh api` command is in the ADR. | `test_adr_names_the_required_status_checks_as_the_job_names`, `test_workflow_has_exactly_the_check_and_integration_jobs` |
| Actions are SHA-pinned, the token is `contents: read`, there are no secrets, no `if:` and no `continue-on-error`. | `test_every_action_is_pinned_to_a_commit_sha[*]`, `test_workflow_token_is_read_only`, `test_no_job_or_step_can_be_skipped_or_allowed_to_fail[*]` |
| `pyyaml` and `types-pyyaml` are in the root `dev` dependency group. | `pyproject.toml`, `uv.lock` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| DEC-5 @ MS-0 | dev machine (decision record), 2026-11-10 | pass | `docs/adr/0001-ci-host.md` | yes (F-3 is the last prover); `python3 scripts/plan/plan.py check` gives `plan ok: 67 goals, 34 ledger rows` |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` at 736bd73 | exit=0; `135 passed, 67 subtests passed`; `make check: all passed` |
| Integration | `make test-int` | exit 0, `No tasks were run` (F-2 creates the targets) |
| Workflow lint | `uvx --from actionlint-py actionlint .github/workflows/ci.yml` | exit=0 |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-1 | `make check` = plan check → tooling tests (ruff/mypy/pytest on `tools/`) → nx lint/typecheck/test | yes: the new guard runs inside `tooling-test`, and the deliberate failure turned `make check` red |
| F-1 | `make test-int` runs `nx run-many -t test-int` and exits 0 with no tasks | yes |
| F-1 | Nx cache lives in `.nx/cache` | yes (`nx.json`), and the CI cache path matches it |

## Mutation checks
Full output: `docs/evidence/F-3/mutation-checks.txt`. Script: `docs/evidence/F-3/mutate.py`, which restores each file (`restored: True` for all). All 14 were caught:
M1 push limited to main → `test_workflow_runs_on_every_push_and_on_pull_requests_to_main` FAILED ·
M2 `needs: check` removed → `test_integration_job_runs_after_check_and_runs_make_test_int` FAILED ·
M3 checkout pinned to `@v7` → `test_every_action_is_pinned_to_a_commit_sha[check]` FAILED ·
M4 job key renamed → 9 failed · M5 `make check` dropped → `test_check_job_bootstraps_then_runs_make_check` FAILED ·
M6 `contents: write` → `test_workflow_token_is_read_only` FAILED · M7 `make test-int` dropped → integration test FAILED ·
M8 ADR payload drops `integration` → `test_adr_names_the_required_status_checks_as_the_job_names` FAILED (the first version of this guard **survived** M8, so it was rewritten to parse the payload) ·
M9 display name changed → `test_workflow_has_exactly_the_check_and_integration_jobs` FAILED ·
M10 `enforce_admins: false` → ADR test FAILED · M11 `continue-on-error` → `test_no_job_or_step_can_be_skipped_or_allowed_to_fail[check]` FAILED ·
M12 `if: false` → `…[integration]` FAILED · M13 cancel on main → `test_superseded_runs_on_the_same_ref_are_cancelled` FAILED ·
M14 restore key not a prefix → `test_nx_cache_is_restored_and_keyed_on_lockfiles[check]` FAILED.

## Review findings left open
A sonnet reviewer ran on `git diff main...HEAD` and found no critical or high issues. I fixed the medium findings on cancelling runs on main and the vacuous `integration` job (noted in the ADR), and the low findings on the restore-key prefix, skippable steps and the irrelevant `gh` rationale. Left open:
- medium · `ci.yml:8-10`: same-repo PRs run twice (push + pull_request). Accepted and recorded in the ADR, because the card says "every push".
- low · `test_ci_workflow.py`: the uv version test accepts any 0.11.x in range, not only the pinned 0.11.6. Harmless.
- low · ADR: branch protection uses the legacy `contexts` field. It still works.
- unverified: whether the action SHAs match their tag comments. They were taken from `git ls-remote --tags` of each upstream repo (peeled commit). The first hosted run will confirm them.

## Left undone, and why
- **Boxes 2 and 3** need a GitHub remote and a push, which are Haneef's call. After that:
  (1) create the repo and push `main`; (2) run the ADR's `gh api … /branches/main/protection` command;
  (3) push a branch with a deliberately failing test, get a red `check` run (link), fix it, get a green run (link);
  (4) once F-2 is merged, the `integration` run link shows the testcontainers Mongo starting.
  Then re-verify F-3, or record it in MS-0's "CI is green on main" check, which allows "no remote yet".
- **Board rebuild.** CLAUDE.md says to run `plan.py build --board` and republish after editing ACCEPTANCE.md. I didn't, because `docs/plan/board.html` is shared across parallel branches. The orchestrator should rebuild it after integrating.

## Found for other goals
- **F-2 / MS-0:** `integration` is green with nothing to test until a project has a `test-int` target. MS-0 should check `nx show projects --with-target test-int` is non-empty. If F-2's `test-int` needs `make up` (compose) and a `.env`, the integration job needs those steps: add them in F-2 and update `test_integration_job_runs_after_check_and_runs_make_test_int`.
- **T-3, C-1, EVAL-1:** add `make test-xt`, `make contracts-check` and `make evals-selftest` to CI (PRD §7.9, §8.2), either through a Makefile target the jobs already call or by changing the guard test.

## Decisions forced (ADRs written)
- `docs/adr/0001-ci-host.md`: GitHub Actions. **The number clashes** with F-1's `0001-nx-uv-monorepo-layout.md`. I kept the card's path because the card and ledger name it. Renumbering is Haneef's call.

## Traps hit
- PyYAML reads the bare key `on:` as boolean `True`, so the test handles both forms.
- The Nx cache key contains `hashFiles('uv.lock', 'package-lock.json')`, which has a space, so restore keys must be split by line, not by whitespace.
- The rtk hook compresses `make` output, so use `rtk proxy make …` to see real result lines.
