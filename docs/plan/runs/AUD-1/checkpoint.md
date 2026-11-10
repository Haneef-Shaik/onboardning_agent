# Checkpoint — AUD-1

- Done: py-data `append_only.py` (`store.append_only("audit_log")`; `tenant/shared/queue` refuse it) and
  `audit.py` (`AuditLog.audit/list/ensure_indexes`, trace id from the current span). Tests in
  `libs/py-data/tests/test_audit.py`; RED in `docs/evidence/AUD-1/red-py-data.txt`.
- Done: contracts `onb_contracts/audit.py`, `GET /audit` (HR_ADMIN), ROLE_MATRIX row, `InMemoryAuditLog`,
  TS client regenerated. RED in `red-api.txt`.
- Done: `apps/api/tests_int/test_mongo_audit.py` (10 live tests, pass on the compose Mongo).
- Done: mutation checks 12/12 (`docs/evidence/AUD-1/mutation-checks.txt`); `make check` exit=0 at c39582c.
- Next: code + security review findings, `make test-int`, `make contracts-check`, handoff, verifier.
- Surprises: py-data now depends on `opentelemetry-api` (uv.lock +5 lines). Machine restarted mid-session
  (reviews re-run).
