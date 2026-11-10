# ADR 0003 — The eval fixture loader seeds the vault through the real `Vault`

- **Status:** accepted (EVAL-1), 2026-11-10
- **Goal:** EVAL-1 · PRD §8.2, §7.10; `evals/README.md` "How a case runs" step 1

## Context

EVAL-1's card and `evals/README.md` say the fixture loader "encrypts `pii_vault_plain` into the real
vault format", so PII checks (`pii_token_binding`, `pii_tokens_allowed_fields`) grade against records
that are encrypted, signed and token-bound exactly as production stores them.

PII-2 added a static guard (`libs/py-crypto/tests/test_vault_boundary.py`): only `apps/api` and
`libs/py-crypto` may import `Vault`, because only humans write the vault (PRD §7.10 Writers). The
harness lives in `libs/py-evals`, so the card and the guard conflict.

Options considered:

1. **Re-implement the record format in the harness.** Duplicates the crypto and the signature, and
   drifts the moment PII-2's format changes. Rejected: it would not be "the real vault".
2. **A seeding helper inside `onb_crypto`.** Passes the guard without touching it, but any module
   could import the helper and write the vault, which is a hidden hole in the boundary. Rejected.
3. **Allow exactly one harness file in the guard, and fence the harness off from production.**
   Chosen.

## Decision

- `libs/py-evals/src/onb_evals/vault_seed.py` is the only harness module that imports `Vault`. The
  guard's allowlist names that one file, not the `py-evals` directory.
- The file writes only to a throwaway eval database: each fixture load generates a fresh random master
  key and fresh org keys, so nothing it writes can be read with production keys.
- A new guard, `libs/py-evals/tests/test_harness_boundary.py`, fails if any module under `apps/` or
  any other library imports `onb_evals`, so the seeding path cannot be reached from a running service.
  It also fails if any other `onb_evals` module imports a vault name.

## Consequences

- The vault boundary still holds for every production path (API-only writers, PRD §7.10).
- PII checks grade real vault records; when PII-2's record format changes, the harness follows with
  no change.
- A reviewer of a future diff that widens the allowlist again has this ADR to compare against.
