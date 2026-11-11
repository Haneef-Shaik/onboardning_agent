# Checkpoint — HIRE-2 · goal/hire-2-encrypted-documents

## Done
1. Contracts `onb_contracts.documents` (DocumentType, DocumentMeta, limits) — tests green.
2. Encrypted GridFS-layout bucket + `documents_meta` store (`apps/api/src/onb_api/documents/`) — 20 unit tests.
3. `POST /hires/{hire_id}/documents` (service, upload checks, route, create_app/main wiring, role matrix row).
4. Cross-tenant ROUTE_CASES row (test-xt 140 passed), Mongo integration tests (33 passed).
5. OpenAPI + TS client regenerated; ADR 0012.

## Next
- `make check`, mutation checks, code-reviewer + security-reviewer, handoff, goal-verifier.

## Surprises
- Hire and document services must share one keyring (one master key) — conftest gained a `keyring` fixture.
- The async Mongo client is bound to the TestClient's loop: integration read-backs open their own store.
