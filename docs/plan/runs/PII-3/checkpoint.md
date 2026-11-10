# Checkpoint — PII-3

Branch `goal/pii-3-pii-detectors-masks`.

## Done
1. Verhoeff and field validators, cross-checked against python-stdnum.
2. Detectors and `redact()`: matching runs on a normalised skeleton, overlaps merge into their union, and every quantifier is bounded. Also the 82-case corpus and 5 hypothesis properties.
3. Masks and blind index in `libs/py-crypto`.
4. 29 mutation checks, all killed (`docs/evidence/PII-3/mutation-checks.txt`).
5. Code review and security review: two rounds, every critical/high fixed. `make check` is green.
6. Handoff written (`handoff.md`).

## Next
- goal-verifier (attempt 1), then set the board to `in review`. Hold for orchestrator review: no rebase, no merge.

## Surprises
- A transcribed Verhoeff table had two wrong rows; python-stdnum caught it, and the rows are now derived.
- A single redaction pass can expose a glued value, so `redact()` loops to a fix point.
- Stale `.pyc` files leaked mutated bytecode into later mutation runs, so the runner now purges bytecode.
- The first review-fix round added a quadratic email walk-back; the re-review caught it.
