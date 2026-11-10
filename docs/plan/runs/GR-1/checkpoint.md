# Checkpoint — GR-1 · Guard every LLM input, output and budget

Branch `goal/gr-1-guardrails-lib` (from main @ e892d97).

## Steps done
1. Injection detector + 33-positive / 30-benign corpus (24 pattern ids); FP rate 1/30 measured and pinned.
2. `GuardedChatModel`: delimiting, injection block, input/output PII redaction, one schema repair then
   `StructuredOutputError`, step/token/time budgets (+ run token budget), every decision audited and
   emitted as `guardrail.outcome`; optional `BedrockGuardrails` (stub client in tests).
3. Coverage 100% on `onb_guardrails`; mutation checks 26/26 killed (`docs/evidence/GR-1/mutation-checks.txt`).

4. Code + security review: highs/mediums fixed in b7fd8f0 (21/22 regression tests RED on the old source);
   mutation checks 38/38; `make check` and `make evals-selftest` green; handoff written.

## Next
goal-verifier pass (attempt 1), then board -> in review (hold for Haneef).

## Surprises
- A module-level `trace.get_tracer()` proxy binds to the first provider; the test fixture swaps providers,
  so the tracer is looked up per call.
- `ruff` S105 flags an enum member named `PASS`; it is `GuardrailOutcome.PASSED` (value `"pass"`).
