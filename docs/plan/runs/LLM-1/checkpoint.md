# Checkpoint — LLM-1 · Plug LLM providers in through a connector registry

Branch `goal/llm-1-llm-connectors` (from main @ 185b674). Board: claimed.

## Done
- Tests first (f8e0b31; RED in `docs/evidence/LLM-1/red-unit-tests.txt`), then `libs/py-llm`:
  protocol, entry-point registry, `config/llm.yaml` + interpolation, resolution, `LLMRuntime`
  (fail fast), `llm.chat` spans, `bedrock` / `langchain` / `fake` connectors, `onb_llm.health`.
- `make llm-health CONNECTOR=…` and the LLM variables in `.env.example` (b967ff7).
- Mutation checks: 15 of 15 caught (`docs/evidence/LLM-1/mutation-checks.txt`). M14 first
  survived: `git grep -E` on macOS has no `\b`; fixed in ced90ce.
- Box 5 scan: `docs/evidence/LLM-1/model-id-scan.txt`.

## Next
1. Code and security review of `git diff main...HEAD`; fix critical/high.
2. `make check`; handoff; goal-verifier; board → in review (hold for Haneef's review, no merge).

## Surprises
- `ChatBedrockConverse` takes the guardrail as `guardrails=` (alias of `guardrail_config`) and does
  not record `region_name` unless passed; the connector passes it.
- `trace.get_tracer` at import binds to a stale provider across the test fixture's resets; the
  span handler gets the tracer per call.
