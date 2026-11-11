# Checkpoint — HIRE-2 · goal/hire-2-encrypted-documents

## Done
1. Contracts, encrypted bucket + `documents_meta`, upload route + wiring, xt row, Mongo tests, OpenAPI/TS client, ADR 0012.
2. Code + security review; fixes applied (consent gate on `pii_consent`, fail-closed hardening).
3. Mutation checks 34/34 killed; `make check`, `make test-int`, `make contracts-check` green.
4. Handoff written (attempt 1).

## Next
- goal-verifier attempt 1; on pass set the board to `in review` (hold for orchestrator; no rebase/merge).

## Surprises
- Hire and document services must share one keyring (one master key) — conftest gained a `keyring` fixture.
- The async Mongo client is bound to the TestClient's loop: integration read-backs open their own store.
