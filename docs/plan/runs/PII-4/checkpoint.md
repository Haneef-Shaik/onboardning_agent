# Checkpoint — PII-4 · goal/pii-4-signed-grants

## Done
1. `config/pii_sink_policy.yaml` + `onb_crypto.sink_policy` (NEVER_SINKABLE floor). `test_sink_policy.py`.
2. `onb_crypto.grant_keys` (Ed25519, env `PII_GRANT_SIGNING_KEY` / `PII_GRANT_VERIFY_KEYS`) and
   `onb_crypto.grants` (GrantIssuer.issue_grant, GrantVerifier, GrantRejected, RejectReason). `test_grants.py`.
3. `onb_crypto.sink` (SinkResolver.resolve_at_sink, jti single use in `pii_grant_jti`, masked
   `pii_access_log`, GrantAuditRecord). `test_sink.py`: one named test per card rejection.

## Next
4. `tests_int/test_grants_mongo.py` against compose Mongo (indexes, replay race, access log).
5. Mutation checks (`docs/evidence/PII-4/mutate.py`), reviews, gates, handoff, verifier.

## Surprises
- ruff S105 flags enum members named `TOKEN_*`: RejectReason uses NOT_FOUND / NOT_BOUND / SUPERSEDED / PURGED.
- `vault_record.check_id` allows spaces; grants use a stricter id pattern.
