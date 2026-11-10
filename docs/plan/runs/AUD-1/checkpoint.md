# Checkpoint — AUD-1

- Done: py-data `append_only.py` (`store.append_only("audit_log")`; `tenant/shared/queue` refuse it) and
  `audit.py` (`AuditLog.audit/list/ensure_indexes`, trace id from the current span). Tests in
  `libs/py-data/tests/test_audit.py`; RED recorded in `docs/evidence/AUD-1/red-py-data.txt`.
- Next: contracts `onb_contracts/audit.py`, `GET /audit` (HR_ADMIN), role-matrix row, in-memory fake,
  then tests_int (py-data live audit, api live list).
- Surprise: py-data now depends on `opentelemetry-api` (uv.lock +5 lines) for the trace id.
