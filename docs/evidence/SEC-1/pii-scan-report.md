# PII leak scan (SEC-1, `make test-pii`)

Generated 2026-11-12T20:21:07+00:00. Result: **clean**, hits: **0**.

Run `run_8a66be7584b40eaa9d22ad92` for hire `h_3e4349832e728db062d1e2aa`.

## Evidence that the values flowed

| Check | Value |
|---|---|
| run_status | completed |
| tasks_done | ['bitbucket_access', 'collect_docs', 'laptop', 'manager_approval', 'orientation', 'security_training', 'welcome_message'] |
| vault_records | 7 |
| aadhaar_vault_records | 1 |
| mock_outbox_messages | 1 |
| events | 4 |
| sink_resolutions | 3 |
| documents | 1 |
| notes_redacted | True |
| chat_redacted | 1 |
| jaeger_spans | 826 |
| llm_calls | 16 |
| jaeger_traces | 170 |
| assistant_saw_the_redacted_question | True |
| invalid_input_refused_with | 422 |
| api_responses | 22 |

## Items scanned by source

| Source | Items |
|---|---|
| api_responses | 2754 |
| app_logs | 4122 |
| container_logs | 15402 |
| events | 181 |
| jaeger | 44904 |
| llm_inputs | 2884 |
| mock_outbox | 29 |
| mongo | 4178 |
| **total** | **74454** |

Raw values searched: mobile, mobile_updated, email, aadhaar, pan, bank_account, ifsc, account_holder_name, document_text (each in its written forms), plus every PII-3 detector type.

Detector hits wholly inside opaque ids (hex, `ptk_`, UUID), not counted: 31.

Redaction markers glued into ids (over-redaction by the OBS-1 scrubber, not a leak): none.

## Allowed detector hits

| Source | Type | Count | Reason |
|---|---|---|---|
| mongo | EMAIL | 3 | a platform user's login id (PRD §3 users), not a hire's PII |

## Findings

None.
