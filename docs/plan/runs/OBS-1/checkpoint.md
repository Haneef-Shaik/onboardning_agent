# Checkpoint — OBS-1 · goal/obs-1-redaction-processor

## Done
1. Tests first (`libs/py-telemetry/tests/test_pii_scrubbing.py`): RED as a collection error (`docs/evidence/OBS-1/red-scrubbing.txt`).
2. `span_scrub.PiiScrubbingSpanProcessor`, installed by `init_telemetry` as the pipeline's `final_hook`, after every hook and right before export.
3. `log_scrub`: scrubbed record copies; `Logger.makeRecord` patch plus a root-handler filter; fail closed.
4. Review fixes (code and security reviewers), test-first (`red-review-findings.txt`, 15 RED):
   - non-str extras;
   - account-key context;
   - args kept for uvicorn;
   - log scrubbing for the process lifetime;
   - resource, scope and dropped counts;
   - trace ids;
   - withheld placeholders.
5. 34/34 mutations killed (`mutation-checks.txt`).
6. Gates: `make check` and `make test-int` green.

## Next
Handoff, goal-verifier, board `in review`, `make down`. Hold for the orchestrator (no merge).

## Surprises
- `logging.makeLogRecord` calls the record factory, which recursed through the first design.
- PII-3's email detector takes `_`, `.`, `?` and `=` into the local part, so `hire_<email>` becomes `[EMAIL]` as a whole.
- `Span.attributes` is a mapping proxy, so the dropped count must come from `span.dropped_attributes`.
