# Checkpoint — AUTH-1 · goal/auth-1-jwt-roles

## Steps done
1. Contracts (`onb_contracts.auth`): Role, LoginRequest, Token, Me (+ envelopes).
2. API auth package (`onb_api/auth`): settings (JWT_SECRET fail-fast), tokens (HS256), passwords
   (bcrypt), users + MongoUserStore (login directory, ADR 0004), dependencies (`authenticated`,
   `require_role`), manager scope, routes (`POST /auth/login`, `GET /me`); data errors → 401/404/400.
3. `user_logins` added to `GLOBAL_COLLECTIONS` (py-data).
4. Unit tests for boxes 1-4; integration test against compose Mongo (`nx run api:test-int`).
5. Mutation checks 13/13 (`docs/evidence/AUTH-1/mutation-checks.txt`).

## Next
- `make check` evidence, code + security review, handoff, goal-verifier.

## Surprises
- Copying py-data's tunnel fixture with the same label made the two parallel test-int sessions
  kill each other's tunnel; the API fixture uses its own label key.
- C-1's `HTTPException` handler shows only the code's fixed message, so the login 401 says
  "Authentication required" (no custom text).
