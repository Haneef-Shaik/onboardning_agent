# Checkpoint — F-4 · Trace every Python service with OpenTelemetry

Branch `goal/f-4-telemetry-lib`.

## Steps done
1. Unit tests first (RED: `docs/evidence/F-4/red-unit-tests.txt`), then `onb_telemetry`: settings, attributes,
   propagation, pipeline, setup, testing (b5b7ee8).
2. Integration tests via socat tunnels to the compose collector, Jaeger and Mongo; nx `test-int` target (75fbdd3).
3. Tightened tests after mutations M5/M9 slipped through: FastAPI 0.16x has native tracing (6591ae3).
4. Evidence for boxes 1–4 and mutation checks (3b14ea4).
5. Code + security review fixes: redacting hooks (`RedactingSpanProcessor`, `copy_span`), on_start containment,
   shutdown guard, content-free failure logs, init rollback (RED: `red-review-fixes.txt`; ddd76c9, d0984ed).
6. Evidence refreshed at d0984ed; `make check` and `make test-int` green.

## Next step
Handoff written; run `goal-verifier`, then set the board to `in review` (hold for Haneef's review; no merge).

## Surprises
- FastAPI 0.162 emits its own OTel spans (scope `fastapi`); a server span alone does not prove instrumentation.
- PyMongo's instrumentor caches its command listener for the process; test resets must drop it.
- Ended SDK spans are read-only, so a redaction hook must return a copy.
- W3C Level 2: the SDK sets the random flag, so `traceparent` flags are `03`, not `01`.
