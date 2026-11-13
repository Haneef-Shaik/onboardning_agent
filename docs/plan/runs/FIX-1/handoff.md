# Handoff — FIX-1 · Fix the defects PERF-1 found under load        attempt 1 · goal/fix-1-load-defects · HEAD (see `git log -1`)

**Outcome claimed.** Each of the three defects PERF-1 recorded is fixed, with a test that failed on the code before the fix: a pause and resume, back to back, never fail a run; a reveal racing an update answers 200 (current value) or 409, both audited, never 500; and `GET /templates/drafts` pages like `/hires` and `/runs`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A test pauses and resumes a run back to back many times (including with no delay) and the run is never failed or cancelled; it fails on the code before the fix (test + recorded red run) | yes | `apps/orchestrator/tests/test_pause_resume_race.py` has 7 tests. **(a)** `test_a_pause_and_resume_at_any_read_of_the_run_never_fails_it[1,3]` sweeps a pause→resume pair with no delay between them over each of the first 40 reads of the run in one drive, with 1 or 3 pairs per read. **(b)** `…_while_an_approval_is_pending_keeps_the_approval_and_the_tasks[1,3]` is the PERF-1 case: the run is parked at an approval, the approval stays `pending`, and the decision later completes the run. **(c)** `test_many_pause_resume_pairs_racing_the_worker_never_fail_the_run` runs a concurrent toggler with 0 and 0.5 ms gaps over 25 runs. **(d)** `…_resume_delivery_is_lost[1,3]` covers the review's H1. Every test asserts no `failed`/`cancelled` run, task or approval, and that the run reaches `completed`. **Red:** `docs/evidence/FIX-1/red-pause-resume-final.txt`. The final test against main's `consumer.py`/`runner.py` gives `6 failed, 1 passed`, with `failed: TaskCancelled` and the approval `cancelled`, the same picture as PERF-1; it is `7 passed` on the fix. The first red run, before the review, is in `red-pause-resume.txt`. Green: `uv run pytest apps/orchestrator/tests -q` gives `244 passed`. |
| A reveal racing an update of the same field returns 200 with a current value or a 409, never a 500; both outcomes are audited (test) | yes | `apps/api/tests/test_reveal_race.py`. **(a)** `test_a_reveal_racing_a_finished_update_returns_the_current_value_and_is_audited`: 200 with the newest value, audit `[("success", "race check")]`, and one access-log entry whose token is the hire's current one. **(b)** `test_a_reveal_racing_an_update_still_in_flight_is_a_409_and_is_audited`: 409 `CONFLICT`, audit `[("failed", "the value changed meanwhile")]`, no access-log entry. **(c)** `test_reveals_racing_updates_of_the_same_field_never_answer_500`: 20 reveals, each raced by 0–4 finished updates. Every answer is 200 (current value) or 409, and there is one audit per reveal, success for 200 and failed for 409. **Red:** `docs/evidence/FIX-1/red-reveal-race.txt` shows `3 failed`, each a 500 `INTERNAL_ERROR`. Green: `uv run pytest apps/api/tests -q` gives `888 passed`. |
| `GET /templates/drafts` pages like `/hires` and `/runs`, and `make test-xt` still covers it (test) | yes | The route takes `page` (1–1000) and `limit` (1–100, default 20), the same bounds as `/runs`, and answers `meta.page {total,page,limit}`. Unit: `apps/api/tests/test_template_drafts_paging.py` (7 passed): pages `[3,3,1,0]` in list order with the total; default limit; out-of-range values give 422. Mongo: `apps/api/tests_int/test_mongo_templates.py::test_the_draft_list_pages_over_mongo_within_the_org` (5 org-A drafts plus 1 org-B draft: pages `[2,2,1,0]`, total 5, no overlap). Cross-tenant: `tools/tests_xt/test_xt_api.py::_list_drafts_check` now also checks that `meta.page.total` counts org A only and that paging one draft at a time yields org A's drafts only; `make test-xt` ran inside `make check` with `362 passed` (`docs/evidence/FIX-1/make-check.txt`). Red: `red-drafts-paging.txt`, where the paging constants and parameters do not exist (ImportError). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| A `run.started`/`run.resumed` delivery that meets a paused run which is `running` again raises `TaskStepError("RunResumedMeanwhile", transient=True)`, so the bus retries it; it never fails the run. Only a `cancelled` task (`TaskCancelledError`, a subclass of `RunHaltedError`) fails a still-running run. | `test_pause_resume_race.py`; `test_run_control.py::test_a_task_cancelled_under_a_running_run_ends_the_run_instead_of_leaving_it_running`; M1–M3 |
| `HireService.reveal` answers 409 `CONFLICT` (audited `failed`, reason `the value changed meanwhile`, no access-log entry) when 3 reads of the hire all hold a superseded token. Other vault refusals are still 500 and audited. | `test_reveal_race.py`; M4–M6 |
| `GET /templates/drafts?page=&limit=` returns `meta.page`; the default page is 20 drafts. `TemplateLifecycle.list_drafts()` still returns every draft (the seeder uses it); the route uses `page_drafts`. | `test_template_drafts_paging.py`, tests_int, tests_xt; M7–M8 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — (`plan.py show FIX-1` lists no ledger rows) | — | — | — | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check (incl. test-xt) | `rtk proxy make check` | see `docs/evidence/FIX-1/make-check.txt` (exit 0; `888 passed` api, `244 passed` orchestrator, `362 passed` tools/tests_xt, `13 passed` mcp-hub test-xt) |
| Integration | `rtk proxy make test-int` | see `docs/evidence/FIX-1/make-test-int.txt`. Exit 0 with `Successfully ran target test-int for 10 projects` on the second run. The first run failed one mcp-hub timing test (a span not exported in time), which is outside this diff; it is recorded in the same file. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (regenerated with `make contracts` in c6b45b9) |
| Eval self-test / PII scan / E2E | — | not run. The diff touches no `libs/py-agents`, py-evals, guardrails, prompts or manifests. The only new log line carries hire id, field and token (no value). E2E-1 is not Done. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-2 (ADR 0018) | Run control: pause/resume publish `run.resumed` in the same transaction; a delivery checks the run before each task; at-least-once delivery with retries | yes. The resume always publishes `run.resumed` (`apps/api/src/onb_api/runs/control.py:206`). The bus retries any handler exception except `ReleaseJob` (`libs/py-eventbus/src/onb_eventbus/worker.py:193`). |
| HIL-1 (ADR 0020) | Approvals pending on a failed or cancelled run are cancelled | yes. That is why PERF-1 saw the approval cancelled; with the fix the run is no longer failed, so the approval stays pending (test b). |
| PII-2 / HIRE-1 | The vault raises `SupersededTokenError` for a token an update replaced; updates commit the newer token to the hire | yes (`libs/py-crypto/src/onb_crypto/vault.py:306`; `HireService.update`) |
| PERF-1 | The three defects and their evidence | yes, all three reproduced by the red runs |

## Mutation checks
`docs/evidence/FIX-1/mutation-checks.txt` (script `docs/evidence/FIX-1/mutate.py`):
- M1: any halt under a running run fails it (the old behaviour) → killed by `test_pause_resume_race.py` (4 tests).
- M2: a halt under a resumed run returns instead of retrying → killed by `…_resume_delivery_is_lost[1]`, `[3]`.
- M3: a cancelled task raises a plain `RunHaltedError` → killed by `test_a_task_cancelled_under_a_running_run_ends_the_run_instead_of_leaving_it_running`.
- M4: a superseded token is a vault failure again → killed by all 3 reveal-race tests.
- M5: `_REVEAL_READS = 1` → killed by `test_a_reveal_racing_a_finished_update_returns_the_current_value_and_is_audited`.
- M6: the 409 is not audited → killed by `…_in_flight_is_a_409_and_is_audited` and `…_never_answer_500`.
- M7: the route ignores `limit` → killed by `test_the_draft_list_pages_and_reports_the_total`.
- M8: the Mongo store does not skip → killed by `test_the_draft_list_pages_over_mongo_within_the_org`.
- M9: the `_id` tie-breaker is removed → **survived**. Ties need two drafts with the same millisecond `updated_at`, and Mongo returns tied rows in a stable order on a small collection, so no deterministic test can see the overlap. The tie-breaker is kept as defence.

## Review findings left open
(Code and security review by an independent agent on `git diff main...HEAD`. No critical findings. H1 fixed: test 3660385, fix in the commit after it. L2 fixed: the index comment.)
- MEDIUM · `apps/orchestrator/src/onb_orchestrator/consumer.py` (post-drive `complete`): reported as "swallows every transition error". This is obsolete, because the review fix restored the original re-raise.
- MEDIUM · `apps/api/src/onb_api/hires/service.py` `_read_current`: the 3 re-reads have no wait between them. For an update that has written the vault but not yet the hire, the reveal is a 409 almost every time. Left as is: it fails closed and is audited, and the 409 tells the client to retry. A backoff would add latency to the request path for a rare race.
- LOW · `templates/store.py` `page_drafts`: `count` and `find` are two reads, so `total` can be off by one under concurrent writes. Same as `/runs` and `/hires`.
- LOW · `GET /templates/drafts` now defaults to 20 drafts. Nothing in `apps/web` calls it. `tools/perf/latency.py` only times it.

## Left undone, and why
Nothing in scope.

## Found for other goals
- RUN-2's open LOW, "two drivers can briefly share a run after a quick pause/resume", still stands. With this fix both drivers keep the run alive (the halted one is retried, the resume's drives). Overlap on one LangGraph thread is as before; no failure was seen in 244 orchestrator tests or the sweeps.
- DEP-1 / DEMO-1: rerun `tools/perf/run.sh` on the VM; the pause/resume and concurrent-reveal phases should now pass without the PERF-1 failures.

## Decisions forced (ADRs written)
None. ADR 0029 (reserved) is not used. Each fix stays within its owner's existing ADR: the run-control semantics in ADR 0018 are unchanged (a pause still stops new work), and a halted job is now retried rather than failing the run.

## Traps hit
- mcp-hub `test_int_every_registered_agent_without_the_mock_tool_is_denied` flaked once on span-export timing under load; it passed on the re-run.
- The `rtk` hook truncates `make` output; run gates as `rtk proxy make …`.
- A test harness that calls a handler once hides the bus's retries. The race tests deliver like `onb_eventbus.worker` (any exception is retried, 5 attempts), otherwise "raise to retry" looks like a failure.

## Verification
goal-verifier attempt 1 on bd17a2e: **pass**. All 3 boxes are met. Red runs were reproduced by loading main's `consumer.py`/`runner.py` (6 failed) and `service.py` (3 failed) through PYTHONPATH. `make check` (test-xt 362 + 13), `make test-int` (10 projects) and `make contracts-check` were all green. The verifier also ran `make test-pii`: 4 passed. No problems found.
