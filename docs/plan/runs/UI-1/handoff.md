# Handoff — UI-1 · Web shell with login, roles and masked fields        attempt 1 · goal/ui-1-web-shell · evidence at 83ca006 (see `git log -1` for the handoff commit)

**Outcome claimed.** `apps/web` (React 19, Vite 8, TypeScript, shadcn/ui primitives on Tailwind 4, TanStack Query, React Router 7) signs users in against the real API, keeps the token in memory only, shows each role its own nav and guards routes by the same table, renders sensitive values only through `MaskedField` (`@onb/ts-ui`), turns envelope errors into user text, and calls the API only through `@onb/ts-api-client`. In dev, Caddy serves it at `https://localhost` (`/api/*` to the API, the rest to Vite). Playwright is set up.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Vitest tests cover `MaskedField` and role-based nav | yes | **MaskedField:** `libs/ts-ui/src/masked-field.test.tsx`, 10 tests: masked by default, a `ptk_` token is never rendered (any case, anywhere, as `masked` or `revealed`), no reveal control without `onReveal`, the revealed value only when passed, an empty reveal stays masked. **Role nav:** `apps/web/src/nav/nav.test.tsx`: exact sections per role, no Hires for MANAGER, Admin for HR_ADMIN only, `canAccess` (sub-paths, lookalike prefixes, unknown paths, agreement with the nav), `safeReturnPath`, `RoleNav` links and `aria-current`. `apps/web/src/App.test.tsx`: sign in as each role → that nav; a refused section by URL → "Not available"; sign-out, a second user on the same tab, token expiry and a 401 → sign-in page. Commands: `npx vitest run --config libs/ts-ui/vitest.config.ts` → `Tests 11 passed (11)` (cn + MaskedField); `npx vitest run --config apps/web/vitest.config.ts` → `Tests 61 passed (61)`. Both also run inside `make check` (`docs/evidence/UI-1/make-check.txt` lines 817, 834). RED first: masked-field and nav failed on the missing module before the code existed (shown in session); `docs/evidence/UI-1/red-auth.txt` for the auth modules. |
| A Playwright smoke test logs in as each role and sees the right nav | yes | `apps/web/e2e/smoke.spec.ts`, run with `npx playwright test --config apps/web/playwright.config.ts` (also `nx run web:e2e`). Playwright starts the **real API** (`apps/web/e2e/serve_api.py`: AUTH-1's `create_app` with JWT login and `/me`, its in-memory user store seeded with the synthetic `SEED_USERS`, password and JWT secret made up per run) and the Vite dev server. For HR_ADMIN, HR and MANAGER it signs in through the form and asserts the name, the role badge, the exact nav links, the landing page, and that the only API calls were `POST /api/auth/login` and `GET /api/me`. HR is refused `/admin` and MANAGER `/hires` ("Not available"). It also covers a wrong password and a reload signing out (no web storage, no cookie). Direct: `5 passed (3.8s)` (`docs/evidence/UI-1/playwright-direct.txt`). **Through Caddy** (`E2E_BASE_URL=https://localhost:19801`, stack `ob-ui-1`): `5 passed (5.5s)` (`docs/evidence/UI-1/playwright-via-caddy.txt`). Mutation M16 (managers see Hires) fails the MANAGER test. |
| `make check` runs web lint, typecheck and tests | yes | `rtk proxy make check` → `make check: all passed`, exit 0 (`docs/evidence/UI-1/make-check.txt`). It runs `nx run web:lint` (`eslint --max-warnings=0 apps/web`, line 178), `nx run web:typecheck` (`tsc -p apps/web/tsconfig.json && tsc -p apps/web/tsconfig.node.json`, line 314; the node config now covers `vite.config.ts`, `playwright.config.ts` and `e2e/`), and `nx run web:test` (`vitest run --config apps/web/vitest.config.ts`, `Tests 61 passed (61)`, lines 824–834). The same holds for `ts-ui` (11 tests). `Successfully ran target lint/typecheck/test for 17 projects`. The web targets predate UI-1 (C-1); UI-1 makes them cover real code. |
| The token-storage ADR exists | yes | `docs/adr/0002-web-token-storage.md`: the token stays in memory only, a reload signs out, the session ends at `expires_at` and on a 401 to a signed-in call, only the client middleware sends the token, the role comes from `/me`. Rejected: web storage, an HttpOnly cookie (needs API work, recorded for SEC), refresh tokens (PRD §7.8). The number clash is in the ADR header and below. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Render every sensitive value with `<MaskedField label masked revealed? onReveal? onHide? />` from `@onb/ts-ui`. Pass `onReveal` only for roles allowed to reveal (UI-4: HR/HR_ADMIN, Aadhaar HR_ADMIN only). Pass `revealed` only with the value from an audited reveal; clear it after the TTL. A `ptk_` token never renders. | `masked-field.test.tsx`, M1–M3, M17 |
| One nav table, `apps/web/src/nav/nav.ts` (`NAV_ITEMS`, `navFor`, `canAccess`, `sectionOf`). Every section path is a placeholder `SectionPage` naming its goal. **To build a screen**, replace the `Guarded` fallback in `App.tsx` with a real `<Route path="/hires/*">` inside the guard. Keep `canAccess` in front of it, and update `EXPECTED` in `nav.test.tsx` if the sections change. | `nav.test.tsx`, `App.test.tsx`, M4–M7 |
| Sections: Runs (UI-5), Approvals (UI-6), Templates (UI-2) and Change requests (UI-3) for all roles; Hires (UI-4) for HR_ADMIN and HR; Admin (UI-8) for HR_ADMIN. The nav is not the security boundary: the API's role matrix is. | `nav.test.tsx` |
| Call the API with the `ApiClient` from `createAppClient` (`apps/web/src/api.ts`): it adds `Authorization: Bearer`. A 401 for the token in use ends the session with the "Your session has ended" notice; a late 401 for an older token, a login 401 and a 403 do not. Add helpers next to `fetchMe` as `unwrap(await client.GET(...))`. In components, read the client from where `App` gets it (props today). A later goal may add a `useApiClient` context. | `api.test.ts`, M10–M12, M18 |
| `useAuth()` gives `{status, me, notice, login, logout, retry}`. `me.role` is the stored role from `/me`. On every session end the TanStack Query cache is cleared, so no data from one user reaches the next on the same tab. | `App.test.tsx` "shows the next user only their own profile…" |
| Show errors with `<ErrorNotice error={e} />` or `describeError(e)`. API codes map to fixed text, validation details list `field: issue`, the trace id shows as "Reference". A non-API error never shows its text. | `errors.test.ts`, M13, M14 |
| The token is in memory only (ADR 0002). Playwright tests must sign in through the UI and move within the app with `history.pushState` + `popstate`, because `page.goto` reloads the page and signs out. | `smoke.spec.ts` |
| Dev serving: `make up`, then the API on the host at `API_DEV_UPSTREAM` (default `host.docker.internal:8000`, e.g. `uvicorn onb_api.main:app`) and `npx vite --config apps/web/vite.config.ts` (port `WEB_PORT`, default 5173, `/api` proxied to `WEB_API_TARGET`). Open `https://localhost:<CADDY_HTTPS_PORT>`. Plain HTTP serves only `/healthz`. | `docs/evidence/UI-1/caddy-routes.txt`, `test_compose_static.py` (2 new tests), M15, M20 |
| Playwright: `nx run web:e2e`. Ports `E2E_API_PORT` (18731) and `E2E_WEB_PORT` (18732); set `E2E_BASE_URL` to go through Caddy. Artifacts go to `dist/playwright/web` (git-ignored). Failure traces hold the per-run password and tokens: never copy them into `docs/evidence`. | `playwright.config.ts` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none) | | | `plan.py show UI-1` lists no ledger rows for this goal | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit 0; lint, typecheck and test for 17 projects; web `61 passed`, ts-ui `11 passed` (`docs/evidence/UI-1/make-check.txt`) |
| Integration | `make test-int` (stack `ob-ui-1`, ports 19800/19801) | exit 0. Stack `12 passed`, including F-2's Caddy checks (`/healthz` on HTTP and HTTPS, Jaeger auth, unknown host 404) with the new Caddyfile; `Successfully ran target test-int for 6 projects` (`docs/evidence/UI-1/make-test-int.txt`) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (no API model changed; `ts-api-client` only gained type re-exports: `LoginRequest`, `Me`, `Role`, `Token`) |
| Playwright (UI-1) | `npx playwright test --config apps/web/playwright.config.ts`, direct and with `E2E_BASE_URL=https://localhost:19801` | `5 passed` both ways |

`make test-xt` (T-3), `make test-pii` (SEC-1) and `make e2e` (E2E-1) do not exist yet. UI-1 adds no route.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| C-1 | `createApiClient` defaults to `/api`; `unwrap` returns data or throws `ApiError {code, status, message, traceId, details}` | yes; the client middleware is added with `client.use` |
| C-1 | Set a Vite dev proxy for `/api` and a Caddy route for `/api/*` | yes, both strip the prefix (the API's paths have none) |
| AUTH-1 | `POST /auth/login` → `{access_token, token_type, expires_at}`; `GET /me` → `{user_id, org_id, role, name, email}` with the stored role; every 401 reads "Authentication required" | yes; the UI tells a login 401 from a session 401 by whether a token was sent |
| AUTH-1 | `create_app(auth=, users=)`; `onb_api.testing` (`InMemoryUserStore`, `SEED_USERS`, `email_of`) | yes, used by the e2e API server (test-only) |
| F-2 | Only Caddy publishes ports; `.env.example` lists every compose variable | yes; `API_DEV_UPSTREAM` and `WEB_DEV_UPSTREAM` added, with defaults, so existing `.env` files keep working |

## Mutation checks
`uv run --frozen python docs/evidence/UI-1/mutate.py --write` → `docs/evidence/UI-1/mutation-checks.txt`, `SUMMARY: 21 of 21 mutations caught`. Every file was restored byte for byte. The named failing tests are in the file. In short:
- M1 `maskedText` shows a token → `maskedText > never shows a vault token…` and `MaskedField > never renders a token…` FAIL.
- M2 the field always counts as revealed → `renders masked by default, with its label` (+3) FAIL.
- M3 reveal control without `onReveal` → `shows no reveal control unless the caller passes onReveal` FAIL.
- M4 Hires for every role → `navFor > never shows Hires to a manager…`, `RoleNav > renders the links for MANAGER…` and the App MANAGER sign-in FAIL.
- M5 Admin for HR → `navFor > shows Admin to HR_ADMIN only`, App `refuses a section the role does not have, even by direct URL` FAIL.
- M6 bare-prefix match → `canAccess > does not treat a lookalike prefix as the section` FAIL.
- M7 the guard ignores the role → App `refuses a section the role does not have, even by direct URL` FAIL.
- M8 the token goes to `sessionStorage` → `keeps the token in memory only…` FAIL.
- M9 an expired token stays live → `treats an expired token as no session…` and `refuses a token that is already expired` FAIL.
- M10 a 401 does not end the session → `ends the session and reports it when the API answers 401…` and App `signs out with a notice…` FAIL.
- M11 a login 401 counts as a lost session → `login > does not report a wrong password as a lost session` FAIL.
- M12 no bearer header → 6 App tests FAIL.
- M13 a non-API error's text is shown → `never shows the text of an error that is not from the API` FAIL.
- M14 a failed login shows the session text → `explains a failed login…` and App `shows a wrong password as a login error…` FAIL.
- M15 Caddy does not strip `/api` → `test_dev_caddyfile_serves_the_web_app_and_the_api_on_localhost` FAILED.
- M16 (Playwright) managers see Hires → `smoke.spec.ts › MANAGER signs in and sees the MANAGER nav` failed.
- M17 a token as `revealed` is shown → `never renders a token passed as the revealed value` FAIL.
- M18 a late 401 ends the new session → `ignores a late 401 for an older token…` FAIL.
- M19 a backslash return path is accepted → `safeReturnPath > refuses "/\\evil.example"…` FAIL.
- M20 the app is served on plain HTTP → `test_the_app_and_api_are_not_served_over_plain_http` FAILED.
- M21 an already-expired login token is accepted silently → `fails, rather than silently storing nothing…` FAIL.

## Review findings left open
A code review and a security review (general-purpose agents with reviewer briefs, on `git diff main...HEAD`) found no critical or high issue. Fixed in 83ca006:
- Security medium: the app was on plain HTTP too. It is now HTTPS-only.
- Code mediums: the profile query is now keyed by token and the whole cache clears at session end; tests now cover a second user on the same tab, token expiry and no notice on manual sign-out. Linux `host-gateway` with a loopback bind is documented (bind to the bridge address, never `0.0.0.0`; `WEB_HOST`).
- Lows: a late 401 for an old token; clock skew at login (`ClockSkewError` with its own message); the expiry timer re-check; a backslash return path; `ptk_` anywhere and in `revealed`; the `retry` memo.

Still open:
- low · `apps/web/src/auth/session.test.ts` "memory only" only checks the store. The Playwright reload test is the end-to-end proof. Kept as a unit smoke check.
- low · no security headers (CSP, `X-Frame-Options`, `Referrer-Policy`) in the dev Caddyfile. They belong with the VM Caddyfile (DEP-1). The token never goes in a URL.
- low · FastAPI `/docs` and `/openapi.json` are reachable at `https://localhost/api/docs` in dev. Turn them off outside dev with the deployment Caddyfile (DEP-1).
- low · the Linux dev setup is documented but not exercised; the evidence is Docker Desktop on macOS.
- info · Vite warns that `import "./aliases"` lacks an extension under the future native config loader. Harmless today.

## Left undone, and why
- No Mongo-backed login in the browser test: SEED-1 (seeding users into Mongo) is not done, and the API is not a compose service yet. The smoke runs the real API code with AUTH-1's in-memory store; AUTH-1's `tests_int` prove the Mongo store. The full compose golden path is E2E-1's.
- No `make e2e` target: the card asks only for Playwright to be set up, and `make e2e` (golden path) belongs to E2E-1. UI-1 adds the Nx target `web:e2e`.
- No real screens behind the sections. Each later UI goal builds its own.

## Found for other goals
- **Haneef / plan:** the ADR number 0002 was used twice, by EVT-1 (`0002-cross-org-job-queue.md`) and by this card's `0002-web-token-storage.md`, after AUTH-1's ADR 0004 reserved 0002 for UI-1. The `0001-ci-host.md` that CI's card names will also clash with `0001-nx-uv-monorepo-layout.md`. Renumber, or have cards say "next free number".
- **SEC:** consider moving the token to an `HttpOnly; Secure; SameSite=Strict` cookie: a login `Set-Cookie`, cookie auth in the token dependency, a logout route, CSRF protection. Only `session.ts` and the client middleware would change in the web app (ADR 0002). Add login rate limiting; the UI already maps `RATE_LIMITED`.
- **DEP-1:** add a CSP, `X-Frame-Options: DENY` and `Referrer-Policy` in the VM Caddyfile; serve the built `dist/apps/web` (`vite build`, already working) in the `vm` profile; disable FastAPI docs outside dev.
- **E2E-1:** build on `apps/web/playwright.config.ts`, pointing `E2E_BASE_URL` at Caddy and the API at the compose service. Sign in through the UI in each test (memory-only token).
- **UI-4:** `MaskedField` is ready. Pass `onReveal` only for HR/HR_ADMIN (Aadhaar: HR_ADMIN only), and clear `revealed` after the TTL (FR-P2).
- **UI-8 / AUTH-1 follow-up:** the HR_ADMIN nav has one Admin section; split it into sub-routes under `/admin/*`, which `canAccess` already covers.

## Decisions forced (ADRs written)
- `docs/adr/0002-web-token-storage.md` (the card names this path; see the number clash above).
- Dev serving: Caddy proxies to dev servers on the host (`host.docker.internal`), not to web and API compose services. This keeps hot reload and needs no images. App services in compose come with DEP-1's `vm` profile. Recorded in the Caddyfile and `.env.example` comments; no ADR, as it is dev-only wiring.
- No PRD conflict built.

## Traps hit
- The `rtk` shell hook rewrites `vitest` and `make` output. Use `rtk proxy …` to capture evidence.
- openapi-fetch's `baseUrl: "/api"` is relative, which Node's `Request` rejects in jsdom tests. Tests pass `http://web.test/api`.
- `page.goto` in Playwright reloads the page and so drops the in-memory session. Move within the app with `pushState` + `popstate`.
- Workers re-evaluate `playwright.config.ts`. The per-run secrets are set with `??=` on `process.env`, so the workers and the web servers share them.
- The session was killed once by a machine restart after the deps commit. The worktree was intact and work resumed from the checkpoint.
