# Handoff — PII-4 · Let only orchestrator-signed grants resolve a token at a sink        attempt 1 · goal/pii-4-signed-grants · see `git log -1` (evidence captured at 4748907)

**Outcome claimed.** `onb_crypto.GrantIssuer.issue_grant` signs a compact JWS (Ed25519, `alg: EdDSA`) over
`{iss, org_id, run_id, task_id, subject_id, fields, sink, iat, exp, jti}`, with `exp - iat` ≤ 10 minutes
and a 128-bit `jti` from `secrets`. The key comes from env `PII_GRANT_SIGNING_KEY`. Sinks verify with
public keys only (`PII_GRANT_VERIFY_KEYS`).

`SinkResolver.resolve_at_sink(grant, sink_id, tokens)` checks, in order:
1. the grant (header, signature, claims, expiry);
2. the org, against the tenant context;
3. the sink;
4. that each field is granted;
5. the field → sink policy from `config/pii_sink_policy.yaml`;
6. then it consumes the `jti` once;
7. then the vault resolves each token with the grant's subject, which checks the token binding and the
   record signature.

It writes one masked `pii_access_log` entry per value, then returns the values. Any failure raises
`GrantRejected(reason)`, releases nothing, and emits one `GrantAuditRecord`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Each rejection has a named test: bad signature, expired, wrong sink, another hire's token, another org's token, field not granted, policy-forbidden field (Aadhaar) even when the grant lists it, replayed `jti` | yes | `uv run --frozen pytest libs/py-crypto/tests -v` → `424 passed`, exit=0 (`docs/evidence/PII-4/py-crypto-tests.txt`). Each named test below asserts the `RejectReason`, an empty `pii_access_log` and exactly one audit record (`World.rejection`):<br>• `test_sink.py::test_rejects_a_bad_signature` (BAD_SIGNATURE)<br>• `test_rejects_an_expired_grant` (EXPIRED)<br>• `test_rejects_the_wrong_sink` (WRONG_SINK)<br>• `test_rejects_another_hires_token` (NOT_BOUND)<br>• `test_rejects_another_orgs_token` (NOT_FOUND: an org-B token under an org-A grant)<br>• `test_rejects_a_field_not_granted` (FIELD_NOT_GRANTED, with a policy that would allow the field)<br>• `test_rejects_aadhaar_even_when_the_grant_lists_it` (FIELD_FORBIDDEN_BY_POLICY; the grant is signed with the orchestrator's key directly, since the issuer refuses to make it)<br>• `test_rejects_a_replayed_jti` (REPLAYED)<br>More signature forms in `test_grants.py`: another key, another key claiming our `kid`, a flipped bit, a widened payload, `alg: none`, HS256 keyed with the public key, unexpected headers.<br>Also covered: `test_rejects_another_orgs_grant` (WRONG_ORG), PAN and bank fields, superseded, purged, tampered record, a concurrent replay.<br>Live on compose Mongo: `tests_int/test_grants_mongo.py`, 4 passed in `make test-int` (6 concurrent uses of one grant give 1 win and 5 REPLAYED; another hire's token and another org's token are refused). |
| A successful resolution writes exactly one `pii_access_log` entry holding a masked value only (test) | yes | `test_sink.py::test_a_successful_resolution_writes_exactly_one_access_log_entry_holding_a_masked_value_only` PASSED. It checks:<br>• exactly 1 entry;<br>• `masked == "90XXXXXX01"`;<br>• neither the raw mobile nor its middle digits appear anywhere in the serialized entry;<br>• the exact key set.<br>Also `test_an_email_resolves_at_the_email_sink_with_its_mask`.<br>Live: `test_a_resolution_writes_one_masked_access_log_entry_in_mongo` reads the collection back with the root client: 1 entry, masked, `org_id` set, and the replay adds none. |
| Mutation check on the subject-binding check (output recorded) | yes | `docs/evidence/PII-4/mutation-checks.txt`, made by `docs/evidence/PII-4/mutate.py`. It ends `source diff after all mutations: 0 files differ`, `survivors: none`, exit=0.<br>• **G1:** the vault no longer compares the record's subject (`vault.py`). `test_sink.py::test_rejects_another_hires_token` FAILED, plus 5 other sink tests and `test_vault.py::test_resolving_with_the_wrong_subject_or_field_is_refused`.<br>• **G2:** the sink resolves with the token's own subject instead of the grant's. `test_rejects_another_hires_token` and `…_mixed_in_with_the_right_one` FAILED.<br>All 17 mutants were killed (see Mutation checks). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Keys.** Make a pair with `uv run python -m onb_crypto.grant_keys`. Only the orchestrator gets `PII_GRANT_SIGNING_KEY` (base64 32-byte Ed25519 seed). Every sink gets `PII_GRANT_VERIFY_KEYS` (comma-separated base64 public keys; more than one during rotation). `GrantSigningKey.from_env(os.environ)` and `GrantVerifier.from_env(os.environ)` raise `GrantKeyError` at startup if a key is missing or malformed. Neither key is in `.env.example` yet, because no compose service uses them. | `test_grants.py::test_keys_load_from_the_environment`, `test_a_missing_or_malformed_*_key_fails_at_startup`, `test_rotation_a_verifier_with_two_keys_accepts_both` |
| **Issue (RUN-3).** `GrantIssuer(key, load_sink_policy(Path("config/pii_sink_policy.yaml"))).issue_grant(org_id, run_id=, task_id=, subject_id=, fields=[VaultField.MOBILE], sink="notify.telegram", ttl=timedelta(minutes=5))` returns the JWS string. Call it inside `tenant_scope(org_id)`; another org raises `TenantViolationError`. A field the policy doesn't allow at that sink raises `SinkPolicyError`. A TTL outside 1 s–10 min raises `ValueError`. Ids match `[A-Za-z0-9_.:-]{1,128}`. | `test_grants.py` (issuer tests) |
| **Resolve (NOTIF-1).** `SinkResolver.create(keyring, store, GrantVerifier.from_env(env), policy, audit)` builds the resolver; the sink never holds a `Vault`. `await resolver.resolve_at_sink(grant, "notify.telegram", [SinkToken(VaultField.MOBILE, ptk)])` returns `{ptk: value}`, or raises `GrantRejected` with `.reason: RejectReason`. Store failures also become `GrantRejected(VAULT_ERROR)`. It is all or nothing; at most 32 distinct tokens per call. | `test_sink.py` |
| **Tenant scope.** `resolve_at_sink` runs in the caller's `tenant_scope`, which must come from the caller's own signed context (the hub-verified agent token), **never from the grant**. A grant for another org is `WRONG_ORG`. | `test_rejects_another_orgs_grant` |
| **Indexes.** Run `await ensure_grant_indexes(store)` at startup. It creates a unique `{org_id, jti}` on `pii_grant_jti`, `{org_id, expires_at}`, and `{org_id, subject.id, at}` on `pii_access_log`. Without the unique index, sequential replays are still refused (a lookup comes first), but **concurrent** replays are not. | `tests_int::test_concurrent_uses_of_one_grant_have_exactly_one_winner`, `test_the_grant_indexes_exist_and_lead_with_org_id` |
| **Audit.** `GrantAudit` is a protocol: `async record(GrantAuditRecord)`. `record.to_document()` gives the AUD-1 shape `{actor: {type: "service", id: sink}, action: "pii.grant.resolve", target: {type: "pii_grant", id: jti, run_id, task_id, subject_id, tokens}, outcome: "rejected", reason, detail, at}`. The claims appear only when the grant's signature verified. `tokens` holds only well-formed `ptk_` tokens, never whatever else was presented. A failing audit store is logged, and the rejection still raises. | `test_the_audit_record_*`, `test_a_raw_value_put_where_a_token_belongs_never_reaches_the_audit_record`, `test_a_failing_audit_store_does_not_turn_a_rejection_into_a_release` |
| **`pii_access_log` resolve entry.** `{org_id, kind: "resolve", actor: {type: "service", id: sink}, sink, run_id, task_id, jti, subject: {type: "new_hire", id}, field, token, masked, at}`. HIRE-1's reveal entries should use the same `kind`, `actor`, `subject`, `field`, `token`, `masked` and `at` keys. | box 2 test |
| **Policy.** `load_sink_policy(path)` gives an immutable `SinkPolicy`: `.allows(field, sink)`, `.fields_for(sink)`, `.sinks`. A config that lists Aadhaar, PAN, `bank_account`, `ifsc` or `account_holder_name` for any sink is refused at load (`NEVER_SINKABLE`). TPL-1 can use this for the FR-T3 publish check. | `test_sink_policy.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-PII-INT @ MS-6 | compose, 2026-11-10 | contributes evidence: forged and widened grants are rejected | `py-crypto-tests.txt`, `make-test-int.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/PII-4/make-check.txt`). py-crypto `TOTAL 1414 39 97%`; `sink.py` 95%, `grants.py` 97%, `grant_keys.py` 95%, `sink_policy.py` 96%. |
| Integration | `make test-int` | exit=0 (`docs/evidence/PII-4/make-test-int.txt`). py-crypto `10 passed`, 4 of them in `test_grants_mongo.py`. Compose project `ob-pii-4`, Caddy on 127.0.0.1:19300/19301. |

test-xt, contracts and evals don't apply: this diff adds no route, MCP tool, event consumer or agent.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| PII-2 | `vault.resolve(token, org_id=, subject_id=, field=)` raises `TokenNotFoundError` / `TokenBindingError` / `SupersededTokenError` / `VaultIntegrityError`, and refuses superseded tokens by default | yes; each maps to a `RejectReason` (`test_rejects_another_hires_token`, `…_superseded_token`, `…_tampered_vault_record`) |
| PII-2 | A sink must treat any `CryptoError` as "send nothing" | yes; it becomes `VAULT_ERROR` (`test_a_keyring_failure_is_a_vault_error_rejection`) |
| PII-2 | Only `apps/api` and `libs/py-crypto` import `Vault` | yes; `SinkResolver.create` builds the vault inside py-crypto, and `test_vault_boundary.py` passes |
| PII-1 | `masks.mask` gives the PRD masks | yes (`90XXXXXX01`, `p***@example.com`) |
| T-1 | `TenantRepository` scopes every insert and find, and `ensure_index` needs an `org_id` prefix | yes; live tests run through `connect()` |

## Mutation checks
`docs/evidence/PII-4/mutation-checks.txt`: 17 mutants, all killed, `0 files differ` afterwards.
- **G1/G2** subject binding (vault compare / sink uses the token's own subject): `test_rejects_another_hires_token` failed.
- **G3** signature never checked: the bad-signature tests failed.
- **G4** any header accepted: `test_bad_signature_an_unexpected_header_is_rejected` failed.
- **G5** exp ignored: `test_expired_grant_is_rejected` and `test_rejects_an_expired_grant` failed.
- **G6** lifetime cap dropped: `test_expired_a_signed_grant_living_longer_than_ten_minutes_is_rejected` failed.
- **G7** wrong sink: `test_rejects_the_wrong_sink` failed.
- **G8** org not compared: `test_rejects_another_orgs_grant` failed.
- **G9** field not granted: `test_rejects_a_field_not_granted*` failed.
- **G10** sink policy skipped: `test_rejects_aadhaar_even_when_the_grant_lists_it` and the PAN/bank tests failed.
- **G11** never-sinkable floor dropped: `test_a_config_that_grants_a_never_sinkable_field_is_refused[*]` failed.
- **G12** jti never consumed: `test_rejects_a_replayed_jti` failed.
- **G13** duplicate-key race not mapped: `test_rejects_a_concurrent_replay_through_the_unique_jti_index` failed.
- **G14** purged released: `test_rejects_a_purged_token` failed.
- **G15** raw value logged: the box-2 test failed.
- **G16** issuer skips the policy, and **G17** issuer skips the TTL cap: the matching issuer tests failed.

RED before GREEN: `red-sink-policy.txt` (ImportError), `red-grants.txt` (collection error), `red-sink.txt` (collection error). The review fixes were also written test-first: 9 failed, then 424 passed.

## Review findings left open
A code review and a security review (two general-purpose subagents) were run on `git diff main...HEAD`. Neither found anything CRITICAL. The one HIGH, a raw value reaching the audit record through `tokens`, is fixed and tested. Also fixed: store failures not audited (MEDIUM), non-ASCII grants crashing with `UnicodeEncodeError` (MEDIUM), duplicate tokens, sub-second TTLs, out-of-range NumericDates, and `check_id` made private.

Still open:
- **MEDIUM · `sink.py` `ensure_grant_indexes`.** Concurrent replay safety needs the unique index. No service calls it yet, because no sink service exists. NOTIF-1 must call it at startup. This is the same pattern as PII-2's `ensure_vault_indexes`.
- **MEDIUM · `pii_grant_jti` grows without bound.** T-1's `DataStore.ensure_index` has no `expireAfterSeconds` option, and a TTL index can't lead with `org_id` anyway. Expired rows are safe to delete, since an expired grant fails `EXPIRED` before its jti is read. This needs a py-data TTL option or a sweep job; it is out of scope here.
- **LOW · A bad token uses up the grant.** The jti is consumed before the vault checks the tokens. This fails closed, but an agent could make its own task fail by adding a bad token. Accepted: the task fails visibly either way.
- **LOW · The sink does not compare `run_id`/`task_id`** with the caller's context. Defence in depth for a grant leaked within its TTL; NOTIF-1 or RUN-3 can pass and compare them.
- **LOW · `issue_grant` returns only the JWS.** RUN-3 can read `jti`/`exp` with `GrantVerifier.verify`, or this can return an `IssuedGrant` later.
- **LOW · `GrantRejected.detail` names the jti and tokens.** These are not secret, but tool errors shown to the LLM should carry `reason` only.

## Left undone, and why
- **Guardrail event on rejection.** PRD §7.10 says a guardrail event and an audit record are written. This goal gives one `GrantAudit` hook that receives every rejection. Wiring it to `audit_log` (AUD-1) and the guardrail stream is NOTIF-1's box ("writes a guardrail event and an audit record").
- **`.env.example` entries for the grant keys.** No compose service reads them yet. The orchestrator goal and NOTIF-1 should add them when they add the services.

## Found for other goals
- **RUN-3:** issue grants only from a template task's `pii_access`. Pass the grant out of band, never in the prompt. Load the policy from `config/pii_sink_policy.yaml`. Convert template field names with `VaultField(name)`.
- **NOTIF-1:**
  - Build the resolver with `SinkResolver.create`.
  - Call `ensure_grant_indexes` at startup.
  - Set the tenant scope from the hub-verified agent token, not the grant.
  - Map `GrantRejected.reason` to the task failure and the guardrail event.
  - Keep the keyring and resolver out of any agent-reachable code path.
- **HIRE-1:** reveal entries in `pii_access_log` should share the resolve-entry keys above (`kind: "reveal"`, a user `actor`, plus `reason`).
- **TPL-1:** check `pii_access` at publish with `load_sink_policy(...).allows(field, sink)`.
- **MCP-1 / T-2:** `pii_grant_jti` belongs with the hard-denied collections (`pii_vault`, `org_keys`, `pii_access_log`) for agents.
- **py-data:** add a TTL index option for expiring collections.

## Decisions forced (ADRs written)
None written.
- **Ed25519 (asymmetric) grants.** The PRD says "a JWS signed with the orchestrator's key". An asymmetric key means sinks can verify but never mint a grant. An HMAC key would let every sink forge one.
- **Hand-written strict JWS.** About 120 lines on `cryptography`, so there is no new JWT dependency and no relock beyond `pyyaml`. It accepts exactly one header and one claim set, which closes alg confusion by construction.
- **jti is single use**, not reusable until expiry. That is how the card's "replayed jti" rejection reads. A task that must resolve twice needs two grants.
- **Field → sink policy checked twice.** The issuer refuses a disallowed field, and the sink refuses it again: the sink's check is the one that counts. `NEVER_SINKABLE` is also hard-coded, so a config edit cannot open Aadhaar, PAN or bank fields.

## Traps hit
- **ruff S105** flags enum members named `TOKEN_*`. The `RejectReason` members are `NOT_FOUND`, `NOT_BOUND`, `SUPERSEDED` and `PURGED`, with values `token_*`.
- **`vault_record.check_id` allows spaces.** Grants use a stricter id pattern.
- **`pyyaml` dependency.** It was added to py-crypto for the policy loader. The `uv.lock` diff is 2 lines; pyyaml was already locked through py-llm.
- **RTK hook.** It truncates captured output. Evidence was captured with `rtk proxy …`.
