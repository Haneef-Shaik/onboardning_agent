# Checkpoint — HIRE-1 · goal/hire-1-hires-reveal

## Done
1. `DataStore.ensure_index(partial=...)` (field-exists filters only, never `org_id`) — 5600c3c.
2. Contracts (`onb_contracts.hires`), the in-memory Mongo stand-in (`onb_api.testing_mongo`), the
   hires package (fields, store, access log, records, service, routes), app wiring, 60 unit tests,
   role-matrix rows — 95a8d6f.
3. `main.py` wires the hire service; startup requires `PII_MASTER_KEY` — 59ba0ab.
4. test-int on the compose Mongo: scan, partial unique index, reveal + access log — 8e3fa84.
5. test-xt cases for all six routes; vault hires seeded lazily (`World.vault_hires()`) — f849a88.
6. ADR 0008; `make contracts` regenerated openapi.json and the TS schema.

## Next
Mutation checks on the named guards, code-reviewer + security-reviewer, `make check`, `make test-int`,
`make contracts-check`, handoff, goal-verifier, board → in review, `make down`.

## Surprises
- seed_v1 hires carry no `email_hash`: a plain unique `{org_id, email_hash}` index would collide on
  the missing key, hence the partial index.
- Several cross-tenant suites count `new_hires` against the seed, so the vault hires are seeded only
  when a hire case first asks for them.
- The generic steering test reused one request three times; a creating route needs a fresh one
  (new unique email) per call.
