#!/usr/bin/env bash
# SEED-1 box 3 evidence: seed passwords come from env; none are committed.
set -u
cd "$(dirname "$0")/../../.."
echo "\$ git rev-parse --short HEAD"
git rev-parse --short HEAD
echo
echo "\$ git grep -nE 'SEED_[A-Z0-9_]*PASSWORD[[:space:]]*[=:][[:space:]]*[^[:space:]]'   # a seed password variable given a value, in any tracked file"
git grep -nE 'SEED_[A-Z0-9_]*PASSWORD[[:space:]]*[=:][[:space:]]*[^[:space:]]'
echo "exit=$? (1 = no match)"
echo
echo "\$ git grep -nE 'SEED_[A-Z0-9_]*PASSWORD' -- ':!docs'   # every tracked mention (names only, outside docs)"
git grep -nE 'SEED_[A-Z0-9_]*PASSWORD' -- ':!docs'
echo
echo "\$ git grep -niE '^[[:space:]]*-?[[:space:]]*password[[:space:]]*:' -- config templates   # a password field in config or templates"
git grep -niE '^[[:space:]]*-?[[:space:]]*password[[:space:]]*:' -- config templates
echo "exit=$? (1 = no match)"
echo
echo "# the values in this worktree's .env (generated locally) appear in no tracked file:"
for v in SEED_HR_ADMIN_PASSWORD SEED_HR_PASSWORD SEED_MANAGER_PASSWORD; do
  val="$(grep "^$v=" .env | cut -d= -f2-)"
  if [ -n "$val" ] && git grep -qF -e "$val"; then echo "$v: FOUND IN A TRACKED FILE"; else echo "$v: value set=$([ -n "$val" ] && echo yes || echo no), in no tracked file"; fi
done
echo
echo "\$ git check-ignore -v .env"
git check-ignore -v .env
echo "\$ git ls-files .env | wc -l"
git ls-files .env | wc -l
