# Checkpoint — HIRE-1 · goal/hire-1-hires-reveal

## Status
In review. goal-verifier attempt 1: **pass** (all five boxes, gates check/integration/cross-tenant
pass, rules ok). Held for Haneef/orchestrator: no rebase, merge or branch deletion from this session.

## Done
1. `DataStore.ensure_index(partial=...)` — 5600c3c.
2. Hires package, contracts, in-memory Mongo stand-in, unit tests, role matrix — 95a8d6f.
3. `main.py` wiring; `PII_MASTER_KEY` required — 59ba0ab.
4. test-int on Mongo — 8e3fa84. test-xt cases for six routes — f849a88.
5. ADR 0008, contracts regenerated — 2ecfa67. py-contracts tests — 5cf5e98.
6. Review fixes (update ordering, version-guarded commit, audit before vault, undo hardening) — 21260c6.
7. Handoff, 32/32 mutation evidence, gates — fe61294.

## Next
Orchestrator: rebase onto main, re-run gates, ff-merge, mark done.
