# 0005 · The web app keeps the access token in memory only

- **Status:** accepted · 2026-11-10 · goal UI-1
- **Number:** the card first named `0002-web-token-storage.md`, but EVT-1's
  `0002-cross-org-job-queue.md` already held 0002, so the orchestrator renumbered this ADR to 0005
  when integrating UI-1 (the card now names 0005).
- **Context:** PRD §7.8: the API issues an HS256 JWT (`sub`, `org_id`, `role`, `exp`) from a password
  login, with no refresh tokens in MVP. AUTH-1 returns it in the response body as
  `{access_token, token_type: "bearer", expires_at}` and reads it from the `Authorization: Bearer`
  header. Tokens live up to `JWT_TTL_MINUTES` (default 60, at most 1440) and cannot be revoked. The
  token's `org_id` scopes every tenant read (§7.9), so a stolen token gives full access to one
  user's org data, including HR's audited reveal of PII (FR-P2), until it expires. The browser must
  hold the token somewhere between calls.

## Decision

1. **The token is held in a JavaScript closure** (`apps/web/src/auth/session.ts`): never in
   `localStorage`, `sessionStorage`, IndexedDB or a cookie. A test checks that a signed-in session
   writes nothing to web storage or `document.cookie`.
2. **A reload, a new tab or closing the tab signs the user out.** The user signs in again. With no
   refresh tokens (§7.8), anything else would mean persisting the token.
3. **The session ends at `expires_at`**: the store refuses an expired token, and a timer clears it
   at expiry, so the UI never sends a token it knows is dead.
4. **A 401 to a signed-in call ends the session** and the sign-in page says
   "Your session has ended. Sign in again." A 401 from the login call itself is a wrong password, not
   a lost session. A 403 keeps the session.
5. **Only the API client sends the token**: `createAppClient` adds the header in an `openapi-fetch`
   middleware. Components never see the token string.
6. **The role in the UI comes from `/me`** (the stored role), not from decoding the JWT. The web app
   never parses the token. The nav is a convenience; the API's role checks are the boundary.

## Alternatives rejected

- **`localStorage` / `sessionStorage`.** Survives reloads, but any script injected into the origin
  (XSS, a compromised dependency) can read it and send it elsewhere, and it outlives the tab
  (`localStorage`). With a 60-minute, unrevocable token that reaches PII reveal, that exposure is
  not worth the convenience.
- **An `HttpOnly; Secure; SameSite=Strict` cookie set by the API.** Out of script's reach, and the
  strongest option once it exists. It needs API changes that are not UI-1's: a `Set-Cookie` on
  login, cookie auth in the token dependency, a logout route, and CSRF protection for unsafe methods
  (SameSite plus an Origin check or a CSRF token). Recorded for SEC (see the UI-1 handoff).
- **A refresh-token pair** (short in-memory access token, refresh in an HttpOnly cookie). The PRD
  rules out refresh tokens for MVP.

## Consequences

- An XSS bug can still use the token while the page is open: memory storage limits theft after the
  fact, not misuse in the moment. Keeping XSS out stays the main defence (React escaping, no
  `dangerouslySetInnerHTML`, a CSP when Caddy serves built assets in the `vm` profile).
- Playwright tests sign in through the UI in every test; there is no stored auth state to reuse.
- If SEC moves to cookies, only `session.ts` and the client middleware change; components use
  `useAuth()` and never touch the token.
