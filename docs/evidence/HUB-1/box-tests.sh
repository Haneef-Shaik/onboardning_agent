#!/usr/bin/env bash
# The named tests behind each HUB-1 Done-when box, unit (no Docker) and integration (testcontainers).
set -euo pipefail
cd "$(dirname "$0")/../../.."
U=apps/mcp-hub/tests
I=apps/mcp-hub/tests_int
echo "== box 1-4, unit (in-memory audit log, in-memory span exporter)"
uv run --frozen pytest -v -p no:cacheprovider --no-header \
  "$U/test_gateway.py::test_an_expired_token_is_denied_with_an_audit_record_and_a_denied_span" \
  "$U/test_gateway.py::test_a_tool_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span" \
  "$U/test_gateway.py::test_a_collection_outside_the_manifest_is_denied_with_an_audit_record_and_a_denied_span" \
  "$U/test_gateway.py::test_a_vault_collection_under_a_forged_manifest_is_denied_with_an_audit_record_and_a_denied_span" \
  "$U/test_policy.py::test_a_vault_collection_under_a_forged_manifest_is_hard_denied" \
  "$U/test_gateway.py::test_an_allowed_call_is_scoped_to_the_token_org_even_when_its_args_name_another_org" \
  "$U/test_server.py::test_end_to_end_the_driver_sees_only_the_token_org" \
  "$U/test_gateway.py::test_a_raw_mobile_in_a_downstream_response_is_redacted_before_it_reaches_the_agent" \
  "$U/test_server.py::test_end_to_end_a_raw_mobile_from_mcp_mongo_is_redacted" \
  "$U/test_scan.py::test_a_raw_mobile_number_is_redacted_from_structured_and_text_content" \
  "$U/test_gateway.py::test_a_downstream_credential_in_a_response_never_reaches_the_agent" \
  "$U/test_server.py::test_end_to_end_credentials_are_injected_by_the_hub_and_never_returned" \
  "$U/test_review_fixes.py::test_an_echoed_call_context_is_withheld_even_though_it_shares_nothing_with_the_key" \
  "$U/test_main.py::test_the_gateway_main_builds_holds_every_credential_it_must_never_return"
uv run --frozen pytest -v -p no:cacheprovider --no-header \
  "libs/py-agents/tests/test_tokens.py::test_the_agent_side_config_holds_only_the_hub_url_and_the_agent_token"
echo "== box 1-4, integration (testcontainers MongoDB, real mcp-mongo with MCP-3 users, real registry and AuditLog)"
uv run --frozen pytest -v -p no:cacheprovider --no-header "$I/test_hub_int.py"
