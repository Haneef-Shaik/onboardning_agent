#!/usr/bin/env bash
# `make seed` (PRD §7.11; SEED-1): runs `python -m onb_api.seed` against this stack's MongoDB.
#
# The Makefile calls it with `.env` loaded. With MONGO_URI set (a VM, through its SSH tunnel) the seed
# uses it as given. Unset or empty, Mongo publishes no port (PRD §7.11), so this opens a short-lived
# loopback tunnel to this compose project's mongo (a socat container on 127.0.0.1, joined to the
# stack's private network, as the integration tests do) and seeds as the Mongo root user. The tunnel
# is removed on exit. Credentials go to the seed through the environment, never through arguments.
set -euo pipefail
cd "$(dirname "$0")/.."

# Pinned by digest: this container carries the root login across the tunnel.
SOCAT_IMAGE="alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
# Label value = the stack's network, so cleanup never touches another compose project's tunnel.
LABEL_KEY="onb.seed-tunnel"

: "${MONGO_DATABASE:?set MONGO_DATABASE in .env}"

run_seed() {
  uv run --frozen python -m onb_api.seed "$@"
}

if [[ -n "${MONGO_URI:-}" ]]; then
  run_seed "$@"
  exit
fi

: "${MONGO_ROOT_USERNAME:?set MONGO_ROOT_USERNAME in .env}"
: "${MONGO_ROOT_PASSWORD:?set MONGO_ROOT_PASSWORD in .env}"

container="$(docker compose ps -q mongo)"
if [[ -z "$container" ]]; then
  echo "seed: the compose mongo service is not running: run \`make up\`" >&2
  exit 1
fi
# One "<network> <ip>" line per network; mongo sits on the stack's private network only, take the first.
read -r network mongo_ip < <(
  docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{"\n"}}{{end}}' "$container" |
    head -n 1
)
if [[ -z "${network:-}" || -z "${mongo_ip:-}" ]]; then
  echo "seed: could not find the mongo container's network address" >&2
  exit 1
fi
label="${LABEL_KEY}=${network}"

stale="$(docker ps -aq --filter "label=${label}")"
if [[ -n "$stale" ]]; then
  # shellcheck disable=SC2086  # one container id per word
  docker rm -f $stale >/dev/null
fi

# Armed before the tunnel starts: an interrupt between `docker run` and here still removes it (by label).
remove_tunnel() {
  local ids
  ids="$(docker ps -aq --filter "label=${label}")"
  # shellcheck disable=SC2086  # one container id per word
  [[ -z "$ids" ]] || docker rm -f $ids >/dev/null 2>&1 || true
}
trap remove_tunnel EXIT
tunnel="$(docker run -d --rm --label "$label" -p 127.0.0.1::27017 "$SOCAT_IMAGE" \
  TCP-LISTEN:27017,fork,reuseaddr "TCP:${mongo_ip}:27017")"
docker network connect "$network" "$tunnel"
SEED_TUNNEL="$(docker port "$tunnel" 27017/tcp | head -n 1)"
export SEED_TUNNEL

MONGO_URI="$(uv run --frozen python -c '
import os
from urllib.parse import quote_plus
user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
host = os.environ["SEED_TUNNEL"]
print(f"mongodb://{user}:{password}@{host}/?authSource=admin&directConnection=true")
')"
export MONGO_URI
run_seed "$@"
