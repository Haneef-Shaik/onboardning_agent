#!/bin/bash
# NOTIF-1 Done-when boxes 1-3: the named tests, unit and against the compose MongoDB replica set.
# Run from the repo root after `make up`: `bash docs/evidence/NOTIF-1/box-tests.sh`.
set -euo pipefail
set -a
. ./.env
set +a
run() {
  echo "\$ uv run --frozen pytest $*"
  uv run --frozen pytest "$@" -p no:cacheprovider -o addopts= -q -rA 2>&1 | grep -E '^(PASSED|FAILED|ERROR)|passed|failed'
  echo
}

echo "== Box 1: mock_outbox holds only the masked mobile, never the raw value (DB scan test)"
run "apps/mcp-mocks/tests/test_notify_sink.py::test_box1_mock_outbox_holds_only_the_masked_mobile_never_the_raw_value" \
  "apps/mcp-mocks/tests_int/test_notify_against_mongo.py::test_box1_mock_outbox_holds_only_the_masked_mobile_never_the_raw_value_db_scan"

echo "== Box 2: a rejected grant stores nothing, fails the task, writes a guardrail event and an audit record"
run apps/mcp-mocks/tests/test_notify_sink.py apps/mcp-mocks/tests_int/test_notify_against_mongo.py \
  apps/mcp-mocks/tests/test_notify_server.py -k "box2 or rejected_grant_is_a_refusal"

echo "== Box 3: the webhook rejects a wrong secret token and accepts Telegram's documented callback_query (fixture)"
run apps/api/tests/test_notify_routes.py -k box3
