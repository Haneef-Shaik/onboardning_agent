# Checkpoint — PII-3

Branch `goal/pii-3-pii-detectors-masks`.

## Done
1. Verhoeff and field validators (`libs/py-guardrails/src/onb_guardrails/pii/{verhoeff,validators}.py`), with vectors cross-checked against python-stdnum.
2. Detectors and `redact()` (`pii/detectors.py`), the 82-case corpus, and hypothesis property tests.
3. Masks and blind index (`libs/py-crypto/src/onb_crypto/{normalize,masks,blind_index}.py`).

## Next
4. Mutation checks: `docs/evidence/PII-3/mutation-checks.txt`.
5. Code review and security review, then `make check`.
6. Handoff, goal-verifier, board `in review`. Hold for orchestrator review; no merge.

## Surprises
- A transcribed Verhoeff permutation table had wrong rows 4 and 5. python-stdnum caught it. The rows are now derived from the base permutation.
- A single redaction pass can expose a glued value (an account number followed by a PAN), so `redact()` loops to a fix point.
