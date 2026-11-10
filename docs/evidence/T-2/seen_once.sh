#!/usr/bin/env bash
# T-2 box 3 "seen once": plant a tenant index not led by org_id, run `make test-int`, show the index
# check fail, then remove the planted database and run `make test-int` again green.
cd /Users/Adya/personals/onboardning_agent/.claude/worktrees/goal-t-2 || exit 1
set -a
. ./.env
set +a
OUT=docs/evidence/T-2/index-check-seen-once.txt
DB=seen_once_t2
mongosh_eval() {
  docker compose exec -T mongo mongosh --quiet -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" \
    --authenticationDatabase admin --eval "$1"
}
{
  echo "# T-2 box 3: index check seen failing once, $(date -u +%Y-%m-%dT%H:%M:%SZ), compose project $COMPOSE_PROJECT_NAME"
  echo "## 1. plant: db.$DB.hires.createIndex({email_hash: 1}) (no org_id prefix), as root via mongosh"
  mongosh_eval "db.getSiblingDB('$DB').hires.createIndex({email_hash: 1}, {name: 'email_only'})"
  echo "## 2. make test-int with the planted index (expect failure)"
  make test-int > /Users/Adya/.claude/jobs/9d50e5ed/tmp/seen-once-run.log 2>&1
  echo "make test-int exit=$?"
  grep -E "^(FAILED|ERROR) |passed|failed|AssertionError|E +(assert|\+|-|At index|Left contains)" /Users/Adya/.claude/jobs/9d50e5ed/tmp/seen-once-run.log | sed 's/\x1b\[[0-9;]*m//g'
  echo "## 3. remove the planted database"
  mongosh_eval "db.getSiblingDB('$DB').dropDatabase().ok"
  echo "## 4. make test-int again (expect green)"
  make test-int > /Users/Adya/.claude/jobs/9d50e5ed/tmp/seen-once-run.log 2>&1
  echo "make test-int exit=$?"
  grep -E "^(FAILED|ERROR) | passed|failed|Successfully ran" /Users/Adya/.claude/jobs/9d50e5ed/tmp/seen-once-run.log | sed 's/\x1b\[[0-9;]*m//g'
} > "$OUT" 2>&1
rm -f /Users/Adya/.claude/jobs/9d50e5ed/tmp/seen-once-run.log
cat "$OUT"
