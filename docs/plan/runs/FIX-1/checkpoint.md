# Checkpoint — FIX-1 · goal/fix-1-load-defects

## Steps done
1. Box 1: red test `apps/orchestrator/tests/test_pause_resume_race.py` (3f5d8f7, red run in
   `docs/evidence/FIX-1/red-pause-resume.txt`), fix in `consumer.py`/`runner.py` (512aca0).
2. Box 2: red test `apps/api/tests/test_reveal_race.py` (af1b905, red run in
   `docs/evidence/FIX-1/red-reveal-race.txt`), fix in `hires/service.py` (d05804a).
3. Box 3: red test `apps/api/tests/test_template_drafts_paging.py` (4af4a78, red run in
   `docs/evidence/FIX-1/red-drafts-paging.txt`), paging in `templates/*` + tests_int + tests_xt (c6b45b9).
4. `make check` exit 0 at c6b45b9 (`docs/evidence/FIX-1/make-check.txt`, test-xt 362 passed).

5. Review H1 fixed (3660385 red, then fix): a halted delivery under a resumed run is retried (transient).
6. Gates green on the final code; mutation checks 8/9 (M9 explained); handoff written.

## Next
goal-verifier, board → in review, `make down`.

## Surprises
- Root cause of box 1: a delivery that met a paused run raised `RunHaltedError`; HR's resume landed
  before `is_moot` re-read the run, so the halt read as "a task cancelled under a running run" and
  the driver failed the run. The same pair landing on the last task's `complete` raised instead of
  leaving the run to the resume's delivery.
- `rtk` truncates `make` output: run gates as `rtk proxy make …`.
