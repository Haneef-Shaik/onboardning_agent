# Handoff — AUTH-1 · Log in with a JWT that carries org and role        attempt 2 · goal/auth-1-jwt-roles · see `git log -1` (attempt 1 failed only on two unexplained `type: ignore` in test_login.py; fixed by typing the helper as `httpx.Response`, gates re-run)

**Outcome claimed.** HR_ADMIN, HR and MANAGER users log in at `POST /auth/login` with email and
password (bcrypt). They get an HS256 JWT `{sub, org_id, role, iat, exp, iss, aud}`. `GET /me` returns
the user. Every guarded route depends on `authenticated`, which reads the bearer token and runs the
route inside `tenant_scope(org_id)` from the token. `require_role(...)` answers 403 to other roles.
The API refuses to start without `JWT_SECRET`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Wrong password, expired token, tampered token and missing token each return 401 (named tests) | yes | `docs/evidence/AUTH-1/box-tests.txt` (`uv run --frozen pytest -v apps/api/tests/test_login.py …` → `149 passed`, exit=0). Named tests, all PASSED: `test_login_with_a_wrong_password_returns_401`, `test_expired_token_returns_401`, `test_tampered_token_returns_401` (flipped signature), `test_missing_token_returns_401`. Each asserts 401, `UNAUTHENTICATED` in the envelope and `WWW-Authenticate: Bearer`. Related cases: `test_token_signed_with_another_secret_returns_401`, `test_a_malformed_authorization_header_returns_401[*]` (7 cases), `test_login_with_an_unknown_email_answers_exactly_like_a_wrong_password`, and the unit tests in `test_tokens.py` (alg none and HS384/512, missing claims including iss/aud, bad claim values, future `iat`, lifetime above the TTL, wrong issuer or audience). |
| A test proves an `org_id` in body, query or path is rejected or ignored | yes | `apps/api/tests/test_org_from_token.py`, PASSED in `box-tests.txt`. **Body, rejected:** `test_a_login_body_with_org_id_is_rejected` gives 422 `{field: body.<extra>}`, and `test_a_login_body_with_any_tenant_key_is_rejected[org_id,orgId,tenant,org]`. **Query, ignored:** `test_the_route_runs_inside_the_tokens_tenant_scope` puts a probe route on the app that declares an `org_id` query parameter. A globex token with `?org_id=org_acme` sees `current_tenant() == org_globex`; the query value reaches the handler but never the scope. `test_an_org_id_in_the_query_is_ignored[*]` and `test_an_org_id_in_a_header_is_ignored[*]` on `/me` are weaker: `/me` reads no query. **Path:** no route may declare an org path parameter. C-1's `test_routes.py::test_no_request_model_or_parameter_declares_org_id` walks every route and the OpenAPI document and PASSED here, now covering the two new routes. `test_an_org_id_in_the_path_finds_no_route` only shows that such paths do not exist. **Other org's subject:** `test_a_user_of_another_org_cannot_be_reached_by_naming_it` (an acme token with a globex `sub`) gives 401. |
| A role-matrix test covers each role × each guarded route with the expected 200/403 | yes | `apps/api/tests/test_role_matrix.py`, PASSED in `box-tests.txt`. `ROLE_MATRIX` is a hand-written table of every guarded route × {HR_ADMIN, HR, MANAGER}. `test_role_matrix[GET /me-*]` runs each cell. `test_every_route_is_public_or_in_the_role_matrix` fails when a route has no row, and `test_every_guarded_route_takes_the_org_from_the_token` fails when a non-public route lacks the `authenticated` dependency (mutation M12). Today the only guarded route is `GET /me`, which is 200 for all three roles, so the **403 side** is shown on probe routes guarded by `require_role`: `test_require_role_gives_200_to_listed_roles_and_403_to_others[4 probes × 3 roles]`, 12 cases, 5 of them 403 with `FORBIDDEN`. Later goals add their routes as rows (for example AUD-1 `GET /audit`: HR_ADMIN 200, HR 403, MANAGER 403). |
| Startup fails without `JWT_SECRET` (test) | yes | `apps/api/tests/test_auth_settings.py`, PASSED in `box-tests.txt`. `test_startup_fails_without_jwt_secret` runs `python -c "import onb_api.main"` (what `uvicorn onb_api.main:app` imports) in a clean process with `JWT_SECRET` unset and asserts a non-zero exit and `ConfigError: JWT_SECRET is not set` in stderr. Related: `test_create_app_fails_without_jwt_secret`, `test_settings_fail_without_jwt_secret[*]` (unset, empty, blank), `test_startup_fails_with_a_short_jwt_secret` (under 32 bytes; a random sentinel never appears in stderr), and `test_startup_succeeds_with_the_settings`. Mutation M9 (fall back to a default secret) fails 5 of these. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Guard a route with `principal: CurrentPrincipal` (any role), or `Annotated[Principal, Depends(require_role(Role.HR_ADMIN, ...))]`. `Principal` is `{user_id, org_id, role}`. The handler runs inside `tenant_scope(principal.org_id)`, so `store.tenant(...)` calls are scoped with no further work. | `test_the_route_runs_inside_the_tokens_tenant_scope`, `test_the_tenant_scope_opens_with_the_token_and_closes_after_the_route`, M5, M16 |
| **Every new route must get a `ROLE_MATRIX` row** in `apps/api/tests/test_role_matrix.py` (or go in `PUBLIC_ROUTES`), or `make check` fails. | `test_every_route_is_public_or_in_the_role_matrix`, M12 |
| Manager scope: `manager_filter(principal)` returns `{"manager_id": sub}` for MANAGER and `{}` otherwise, for list queries. `require_in_manager_scope(principal, record)` raises `AppError(NOT_FOUND)` for another manager's record (404, not 403). | `test_manager_scope.py`, M7, M8 |
| Data-layer errors that reach a route map as follows: `NoTenantContextError` → 401 (with a WARNING log naming the route), `NotFoundError` → 404, `TenantViolationError` → 400. Their text is never shown. | `test_data_errors.py`, M13 |
| `create_app(*, auth: AuthSettings \| None = None, users: UserStore \| None = None)`. With `auth=None` it reads the environment and fails fast. With `users=None`, login and `/me` answer 503. The lifespan runs `users.ensure_indexes()` and warms the bcrypt stand-in. Tests use `onb_api.testing` (`TEST_SETTINGS`, `InMemoryUserStore`, `seeded_store`, `token_for`, `bearer`), with no `__init__.py` in `apps/api/tests`. | `conftest.py`, `test_startup_makes_the_user_store_indexes` |
| Environment: `JWT_SECRET` (≥32 bytes, required), `JWT_TTL_MINUTES` (1–1440, default 60), `MONGO_URI` and `MONGO_DATABASE` (both required by `onb_api.main`). | `test_auth_settings.py` |
| Users are created with `MongoUserStore(store).add_user(NewUser(email, name, role, password_hash=hash_password(pw, rounds=...)))` inside `tenant_scope(org_id)`. Emails are unique across orgs (`EmailTakenError`). The login directory `user_logins` (global) holds `{_id: sha256(normalised email), org_id, user_id}` only (ADR 0004). | `tests_int/test_mongo_login.py` (8 passed) |
| Tokens carry `iss = aud = "onb-api"`, are HS256 only, allow 5 s of clock skew, and may not live longer than `JWT_TTL_MINUTES`. Access checks use the token's role. `/me` reports the stored role. A role change or removal takes effect when the token expires; there is no revocation. | `test_tokens.py`, `test_me_reports_the_stored_role_not_the_tokens`, M14, M15 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show AUTH-1` lists no ledger rows, and ACCEPTANCE.md does not mention AUTH-1 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. Lint, typecheck and test for 17 projects (`docs/evidence/AUTH-1/make-check.txt`). The known py-guardrails flake did not occur. |
| Integration | `make test-int` | exit=0, `Successfully ran target test-int for 4 projects`. Stack 12, py-data 24, py-telemetry 3, and the new api target 8 passed (`docs/evidence/AUTH-1/make-test-int.txt`). Compose project `ob-auth-1` on Caddy ports 19100/19101. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date`. The client was regenerated for `login`, `getMe` and the HTTPBearer scheme; `web` and `ts-api-client` typecheck and tests pass. |

`make test-xt` (T-3), evals and the PII scan do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| T-1 | `tenant_scope` is a contextvar context manager that resets on exit; `current_tenant()` raises `NoTenantContextError` | Yes. The yield dependency opens it in the request task (scope-reset test, M16). |
| T-1 | By-id `get` is `{_id, org_id}`; a foreign id raises `NotFoundError` | Yes. `test_a_user_of_another_org_is_not_found_in_this_orgs_scope` (live). |
| T-1 | `GLOBAL_COLLECTIONS == {orgs, agent_registry, tool_catalog}` | **Superseded on purpose:** `user_logins` was added (ADR 0004). `test_store.py` asserts the new exact set, and that `users` stays a tenant collection. |
| C-1 | `RequestModel` forbids extra fields; the route walk fails on any org input | Yes. `LoginRequest` subclasses it, and the walk covers the new routes. |
| C-1 | `HTTPException` shows only the code's fixed message and keeps `WWW-Authenticate` | Yes. The login 401 says "Authentication required" for every cause. |
| C-1/F-4 | Telemetry starts before the app is built | Yes, and now also before the Mongo client, so PyMongo spans are recorded (`test_main_initialises_telemetry_as_the_api_service` checks the client's `CommandTracer`). |

## Mutation checks
`docs/evidence/AUTH-1/mutation-checks.txt` (script: `docs/evidence/AUTH-1/mutate.py`) → `16/16 mutations caught`, exit=0. Highlights:
- M2, expiry not checked → `test_expired_token_returns_401`, `test_an_expired_token_is_refused`.
- M3, signature not checked → `test_tampered_token_returns_401`, `test_token_signed_with_another_secret_returns_401`, and 12 more.
- M4, missing token let through as a fixed principal → `test_missing_token_returns_401` and 8 more.
- M5, route not run in the token's scope → `test_the_route_runs_inside_the_tokens_tenant_scope` and 13 more.
- M6, `require_role` does not check → `test_require_role_gives_200_to_listed_roles_and_403_to_others[...]` (5 cases).
- M9, `JWT_SECRET` falls back to a default → `test_startup_fails_without_jwt_secret` and 4 more.
- M10, password not checked → `test_login_with_a_wrong_password_returns_401`.
- M11, login body ignores extra fields → `test_a_login_body_with_org_id_is_rejected` and 6 more.
- M12, new route without a guard or matrix row → both completeness tests.
- M14, lifetime cap removed → 1 test. M15, audience not checked → 29 tests.
- M16, scope never reset (contextvar set directly) → `test_the_tenant_scope_opens_with_the_token_and_closes_after_the_route`, the only test that fails.

Two equivalent mutations were found and replaced. Turning a missing header into an empty token is still refused by `read_token`. `tenant_scope(...).__enter__()` with no reference is collected at once and resets in its `finally`.

## Review findings left open
Code review and security review found no critical or high issues. Fixed: telemetry order, the `tests` package clash, the scope-reset test, the stored role on `/me`, indexes at startup, bcrypt on the event loop, `iss`/`aud`, the lifetime cap, leeway, dummy-hash rounds, the shielded rollback that keeps the original error, a warning log for a missing context, and the short-secret sentinel. Left open:
- **medium** · `auth/users.py`, `mongo_users.py` · Staff emails are stored raw in `users.email`, and the directory key is unkeyed SHA-256, not the HMAC blind index of PRD §7.10. Left: PRD §7.10 governs hire PII in the vault. Whether staff login emails are vault data is a product decision for Haneef, and the keyed blind index belongs to PII-1/PII-3 (platform key). ADR 0004 records the migration path.
- **medium** · `dependencies.py` · No revocation. A removed or demoted user keeps access until `exp` (default 60 min, maximum 24 h). PRD §7.8 asks for a simple JWT with no refresh. SEC (MS-6) plans 15-minute tokens; set `JWT_TTL_MINUTES=15` then.
- **low** · `routes.py` · An unknown email skips one Mongo read, a small timing difference against a known email. Accepted. Rate limiting (SEC, ADR 0003) is the real control.
- **low** · `mongo_users.py` · A cross-org duplicate email raises `EmailTakenError`. A future user-create route must not reveal that the address exists in another org.
- **low** · `users.py` · `casefold()` merges some Unicode addresses (`ß`/`ss`). Add address validation when a user-create route arrives.
- **low** · `store.py` · Any code can call `store.shared("user_logins")`. An import or lint rule allowing only `onb_api.auth.mongo_users` would close this (T-2 owns the safety net).
- **low** · `settings.py` · Only the secret's length is checked; a long placeholder passes. `.env.example` does not list `JWT_SECRET` because the API is not a compose service yet.

## Left undone, and why
- I couldn't set the `/goal` condition from this orchestrated background session, so I worked to the text printed by `plan.py goal AUTH-1`.
- No user-management route: none is in the card's scope. SEED-1 creates users through `MongoUserStore.add_user`.

## Found for other goals
- **SEED-1:** create users with `MongoUserStore.add_user` inside `tenant_scope(org)`, so the login directory stays in step. Seed passwords come from the environment, hashed with `hash_password(pw, rounds=AuthSettings.bcrypt_rounds)`.
- **Deployment/compose goal:** when the API joins compose, add `JWT_SECRET`, `MONGO_URI` and `MONGO_DATABASE` to `.env.example` and the service environment.
- **AUD-1, T-3, HIRE-1, TPL-2, UI-1:** add each new route to `ROLE_MATRIX`. Use `manager_filter` and `require_in_manager_scope` for manager views.
- **T-3:** `apps/api/tests_int/conftest.py` copies py-data's Mongo tunnel fixture under its own label, `onb.test.api-mongo-tunnel`. Sharing the label made the two parallel `test-int` sessions kill each other's tunnel. A shared test-support module would remove the copy.
- **MCP-3:** keep `user_logins` (as well as `users`) away from every MCP database user.
- **PII-1/PII-3:** decide whether staff emails become vault or blind-index data; if so, rekey `login_key` to an HMAC (ADR 0004).
- **SEC:** 15-minute tokens (`JWT_TTL_MINUTES=15`), login rate limiting, and optionally token revocation.

## Decisions forced (ADRs written)
- `docs/adr/0004-login-directory.md`: a global `user_logins` directory finds the org from the email at login. Users stay tenant documents, and emails are unique across orgs. ADR numbers 0002 and 0003 are left for UI-1 and SEC, which name them.
- No PRD conflict built. One open question for Haneef: are staff (non-hire) emails vault data under PRD §5.5? See the open findings.

## Traps hit
- C-1 shows only the fixed message for an `HTTPException`, so a custom login message cannot pass through. Every 401 reads "Authentication required".
- A test package named `tests` in two projects breaks a combined pytest run under `--import-mode=importlib`. Fakes live in `onb_api.testing` instead. The api typecheck now runs mypy twice (tests and tests_int) to avoid two `conftest` modules.
- A FastAPI yield dependency does set the contextvar for the handler, but TestClient runs each request in a copied context. Only a test that drives the dependency in one task can see whether the scope resets.
- PyJWT warns about a 32-byte HMAC key for HS384/512; the tests sign with 64-byte keys.
