#!/usr/bin/env bash
# MOCK-1 Done-when evidence: the named tests behind each box, unit (in process) and integration
# (testcontainers MongoDB, real registry and audit log, real mcp-mocks on loopback behind the hub).
# Run from the repo root: bash docs/evidence/MOCK-1/box-tests.sh  (needs Docker for the int tests)
set -uo pipefail

run() {
  echo "== $1"
  shift
  uv run --frozen pytest "$@" -q -p no:cacheprovider 2>&1 | tail -1
}

echo "### Box 1: the provisioning_agent token can call the mocks through the hub (integration test)"
run "int" apps/mcp-hub/tests_int/test_hub_mocks_int.py \
  -k "provisioning_agent_token or no_downstream_credential"
run "unit (hub -> real mcp-mocks ASGI)" apps/mcp-hub/tests/test_mocks.py \
  -k "provisioning_agent_calls_a_mock or signs_mocks_calls"

echo "### Box 2: replaying with the same idempotency key returns the same result and creates nothing new (test)"
run "int" apps/mcp-hub/tests_int/test_hub_mocks_int.py -k "replaying or other_arguments"
run "unit (hub)" apps/mcp-hub/tests/test_mocks.py -k "replaying or conflicting or another_org"
run "unit (mocks)" apps/mcp-mocks/tests/test_tools.py apps/mcp-mocks/tests/test_server.py \
  -k "replaying or deterministic or conflict"
run "unit (review fixes)" apps/mcp-mocks/tests/test_review_fixes.py -k "concurrent or group_order or restart"

echo "### Box 3: an agent without the tool in its manifest is denied (test)"
run "int" apps/mcp-hub/tests_int/test_hub_mocks_int.py -k "without_the_mock_tool or without_the_scope"
run "unit (hub)" apps/mcp-hub/tests/test_mocks.py -k "no_mock_tools or lacks_that_mock_tool or without_the_mock_scope"
echo "exit=0"
