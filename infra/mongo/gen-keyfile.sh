#!/usr/bin/env sh
# Generates the Mongo replica-set keyfile (F-2; PRD §7.11). The keyfile is git-ignored and never
# committed. Idempotent: an existing non-empty keyfile is kept, since replacing it would lock the
# running replica set out of its own data.
set -eu

KEYFILE="${1:-$(dirname "$0")/keyfile}"

if [ -s "$KEYFILE" ]; then
  echo "gen-keyfile: keeping existing $KEYFILE"
  exit 0
fi

umask 077
openssl rand -base64 756 > "$KEYFILE"
chmod 400 "$KEYFILE"
echo "gen-keyfile: wrote $KEYFILE"
