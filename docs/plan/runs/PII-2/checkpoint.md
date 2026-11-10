# Checkpoint — PII-2 · goal/pii-2-pii-vault

## Steps done
1. `tokens.py`: `ptk_` + ULID + 128 `secrets` bits, 56 chars, Crockford base32 (54bb193).
2. `vault_record.py`, `vault.py`, `vault_indexes.py`: record codec, HMAC `sig`, put / verify /
   resolve / purge, Aadhaar partition + consent, boundary test (505961d).
3. `tests_int/test_vault_mongo.py` on compose Mongo (`ob-pii-2`), 4 passed (df98497).
4. Mutation checks: 19 guards, all killed (`docs/evidence/PII-2/mutation-checks.txt`) (3c7ee8b).

## Next
- Code + security review of `git diff main...HEAD`; fix critical/high.
- `make check`, `make test-int`; handoff; goal-verifier; board → in review; `make down`.

## Surprises
- ruff S105 flags any constant named `*TOKEN*` holding a string: the prefix is `PTK_PREFIX`.
- The purged-shape guard was masked by the `sig` check (V16 survived) until a re-signed test was added.
