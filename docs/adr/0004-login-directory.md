# 0004 · A global login directory to find a user's org at login

- **Status:** accepted · 2026-11-10 · goal AUTH-1
- **Number:** 0002 and 0003 are reserved by UI-1 (web token storage) and SEC (login rate limit).
- **Context:** PRD §7.8 has users log in with email and password; §7.9 says `org_id` is never a
  request input and all tenant data goes through `TenantRepository`, which needs a tenant context.
  At login there is no org yet, so the user's org has to be found from the email alone. `users` is a
  tenant collection (§9). C-1's route walk fails the build if a request declares an org in any form,
  so "send the org slug with the login" is ruled out.

## Decision

1. **`user_logins` is a global collection** (added to `onb_data.GLOBAL_COLLECTIONS`). A document is
   `{_id: sha256(normalised email), org_id, user_id}` and nothing else: no address, name, role or
   hash. Normalised means `strip()` + `casefold()`.
2. **The user record stays a tenant document** in `users` (`{_id, org_id, email, name, role,
   password_hash}`), read with `store.tenant("users").get(user_id)` inside `tenant_scope(org_id)`
   from the directory. A unique `{org_id, email}` index backs it.
3. **An email belongs to one user across all orgs.** The directory `_id` enforces it.
   `MongoUserStore.add_user` writes the directory entry first, then the user, and deletes the entry
   again if the user insert fails (no transaction yet; EVT-1 owns transactions).
4. **Login answers the same 401 for an unknown email, a missing record and a wrong password**, and
   an unknown email still costs one bcrypt check.

## Alternatives rejected

- **Login body names the org.** Contradicts §7.9 Identity and C-1's guard.
- **Scan every org's `users`.** Cost and timing grow with the number of orgs and reveal nothing new
  in return; it also needs a cross-org loop in the API.
- **Make `users` global.** Puts password hashes and roles outside tenant scoping.

## Consequences

- One new global collection. It reveals, to someone with database read access, which org a known
  email belongs to. SHA-256 of an email is a lookup key, not secrecy: a dictionary of addresses can
  be hashed. If staff emails are to be treated like hire PII later, switch the key to an HMAC with a
  platform key from PII-1 (one migration that rehashes the directory).
- MCP-3 should keep `user_logins` away from every MCP database user, like `users`.
- SEED-1 creates users through `MongoUserStore.add_user` so the directory stays in step.
