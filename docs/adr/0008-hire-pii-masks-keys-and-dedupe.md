# 0008 · Hire PII: stored masks, org keys on first write, partial email index

- **Status:** accepted · 2026-11-11 · goal HIRE-1
- **Number:** 0008 by the orchestrator (TPL-2 runs in parallel with 0009).
- **Context:** PRD §7.10 keeps raw values only in the vault and shows every sensitive field masked
  (FR-P1). FR-P5 logs every token resolution in `pii_access_log`. §7.10 Lookup / dedupe makes
  `{org_id, email_hash}` the duplicate check. Three questions the PRD leaves open came up building
  `POST/GET /hires`.

## Decision

1. **Masks are computed once, at write time, and stored on the hire** (`new_hires.pii_masked.<field>`),
   from the validated value. Reading a hire never decrypts. Fields with no PRD mask (IFSC, account
   holder name) show `[masked]`, as PII-4's sink does. A seeded hire whose token has no stored mask
   shows `[masked]`.
2. **An org's keys are made on its first sensitive write** (`Keyring.create` when `active` raises
   `NoOrgKeysError`; a concurrent maker's `KeyVersionConflictError` is harmless). No goal provisions
   org keys today (eval seeding does it for its own scratch databases).
3. **The email index is a partial unique index**: `{org_id: 1, email_hash: 1}`, unique, only over
   documents that have `email_hash`. `DataStore.ensure_index` gains `partial=`, restricted to
   `{field: {"$exists": True}}` on fields other than `org_id`, so an index can never be narrowed by org.
   Hires seeded without an `email_hash` (seed_v1) do not collide on a missing key. Email is required
   on `POST /hires`, so every hire made through the API is deduplicated.
4. **Aadhaar consent and the alternative ID.** An Aadhaar needs `aadhaar_consent.purpose` (stored by
   the vault in `pii_consent`); a hire who declines gives `alternative_id` (passport, voter ID or
   driving licence). Only the type is recorded: the document itself is an encrypted upload (HIRE-2),
   so no unlisted ID number is stored outside the vault.
5. **Free text is redacted before it is stored**: hire `notes`, reveal reasons (in `pii_access_log`
   and `audit_log`) and consent purposes pass through `onb_guardrails.pii.redact`.

## Alternatives rejected

- **Mask at read time by decrypting.** Every list would resolve every token: one `pii_access_log`
  entry per field per hire per page view (FR-P5), and the vault on the hot path of every read.
- **Keep org keys for an org-provisioning goal.** None exists before SEED-1, and HIRE-1 would not
  work on a fresh org. `make seed` can still create them explicitly; creation stays idempotent.
- **A separate `hire_emails` collection for uniqueness.** One more write to keep in step (on email
  update and purge) for what a partial index does.

## Consequences

- A mask is display data, not a secret, but it reveals a little (an email's first letter and domain,
  the last digits of a number), as the PRD's display rules intend. It lives in `new_hires`, which
  agents may read through MCP; PRD §7.3 already gives agents masked data.
- PII-5 (purge) must also clear `pii_masked.<field>` and the blind index when it purges a value.
- Key creation on first write means the first `POST /hires` of an org also writes `org_keys`; the
  API's Mongo role needs insert on `org_keys` (deployment).
- `pii_access_log` is written with inserts only, but is not yet an append-only collection in
  `onb_data` (only `audit_log` is). Making it one is for SEC/AUD.
