#!/usr/bin/env bash
# Plant a "stale" tunnel (this stack's label, an owner pid that is not running), run the py-data
# integration suite, and check the cleanup removed it. Records the result for T-2's evidence.
cd /Users/Adya/personals/onboardning_agent/.claude/worktrees/goal-t-2 || exit 1
set -a
. ./.env
set +a
OUT=docs/evidence/T-2/tunnel-cleanup-check.txt
mongo=$(docker compose ps -q mongo)
network=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$mongo")
dead_pid=999999
while kill -0 "$dead_pid" 2>/dev/null; do dead_pid=$((dead_pid + 1)); done
{
  echo "# T-2 attempt 5: stale-tunnel cleanup spares live sessions, removes dead ones. $(date -u +%FT%TZ), ob-t-2"
  echo "## before the fix: make test-int failed 2 of 3 runs with AutoReconnect (shared tunnel label; a later"
  echo "## session's cleanup killed another live session's tunnel). After: 4 of 4 runs exit 0 (see make-test-int.txt)."
  planted=$(docker run -d --rm --label "onb.test.mongo-tunnel=$network" --label "onb.test.tunnel-owner-pid=$dead_pid" \
    alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055 TCP-LISTEN:1,fork EXEC:true)
  echo "planted stale tunnel ${planted:0:12} with owner pid $dead_pid (not running)"
  uv run --frozen pytest libs/py-data/tests_int/test_tenant_repository.py -q -p no:cacheprovider 2>&1 | tail -1
  if docker ps -a -q --no-trunc | grep -q "$planted"; then
    echo "RESULT: stale tunnel still present (cleanup FAILED)"
    docker rm -f "$planted" > /dev/null
  else
    echo "RESULT: stale tunnel removed by the session's cleanup"
  fi
  echo "tunnels with this stack's label left after the session: $(docker ps -a -q --filter "label=onb.test.mongo-tunnel=$network" | wc -l | tr -d ' ')"
} > "$OUT" 2>&1
cat "$OUT"
