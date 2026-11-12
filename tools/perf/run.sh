#!/usr/bin/env bash
# PERF-1 (PRD §12 Latency): run the latency measurement against this worktree's compose stack.
#
#   make up && make seed            # the stack and the seeded org (once)
#   set -a && . ./.env && set +a && tools/perf/run.sh [latency.py options]
#
# What it starts, on the host, for the length of the run (all removed on exit):
# - loopback tunnels (socat containers joined to the stack's private network, as scripts/seed.sh does)
#   to the compose mongo and otel-collector, which publish no port (PRD §7.11);
# - the API (`uvicorn onb_api.main:app`, one process, the default) on 127.0.0.1:$PERF_API_PORT, which
#   Caddy reaches as API_DEV_UPSTREAM (set it to host.docker.internal:$PERF_API_PORT in .env);
# - the orchestrator worker (`python -m onb_orchestrator`) with the default **stub** executor: no model is
#   ever called. LLM_CONNECTOR is forced to `fake` so nothing here can reach a paid model.
# Then `tools/perf/latency.py` measures through Caddy (https://localhost:$CADDY_HTTPS_PORT/api).
#
# JWT_SECRET and TELEGRAM_WEBHOOK_SECRET are generated per run (they only need to exist); the seed users'
# passwords come from SEED_*_PASSWORD in the environment and are never printed.
set -euo pipefail
cd "$(dirname "$0")/../.."

SOCAT_IMAGE="alpine/socat:1.8.1.3@sha256:e8a6e8868a8e6e01d9c2c321a8f8d4474d2d741d962462e5c5a124a8e66a0055"
LABEL_KEY="onb.perf-tunnel"
PERF_API_PORT="${PERF_API_PORT:-18800}"
LOG_DIR="${PERF_LOG_DIR:-reports/perf}"

: "${MONGO_ROOT_USERNAME:?load .env first}"
: "${MONGO_ROOT_PASSWORD:?load .env first}"
: "${MONGO_DATABASE:?load .env first}"
: "${PII_MASTER_KEY:?load .env first}"
: "${CADDY_HTTPS_PORT:?load .env first}"
[[ "${API_DEV_UPSTREAM:-}" == *":${PERF_API_PORT}" ]] || {
  echo "perf: set API_DEV_UPSTREAM=host.docker.internal:${PERF_API_PORT} in .env and recreate caddy" >&2
  exit 1
}

mkdir -p "$LOG_DIR"
pids=()
label=""

cleanup() {
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  if [[ -n "$label" ]]; then
    local ids
    ids="$(docker ps -aq --filter "label=${label}")"
    # shellcheck disable=SC2086  # one container id per word
    [[ -z "$ids" ]] || docker rm -f $ids >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# tunnel <service> <port>: prints 127.0.0.1:<host port> of a socat tunnel to the service's container.
tunnel() {
  local container network ip id
  container="$(docker compose ps -q "$1")"
  [[ -n "$container" ]] || { echo "perf: compose service $1 is not running (make up)" >&2; exit 1; }
  read -r network ip < <(
    docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{"\n"}}{{end}}' "$container" |
      head -n 1
  )
  id="$(docker run -d --rm --label "$label" -p "127.0.0.1::$2" "$SOCAT_IMAGE" \
    "TCP-LISTEN:$2,fork,reuseaddr" "TCP:${ip}:$2")"
  docker network connect "$network" "$id"
  docker port "$id" "$2/tcp" | head -n 1
}

network_name="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
  "$(docker compose ps -q mongo)" | head -n 1)"
label="${LABEL_KEY}=${network_name}"
stale="$(docker ps -aq --filter "label=${label}")"
# shellcheck disable=SC2086  # one container id per word
[[ -z "$stale" ]] || docker rm -f $stale >/dev/null

mongo_addr="$(tunnel mongo 27017)"
otel_addr="$(tunnel otel-collector 4318)"  # OTLP/HTTP, the SDK default protocol

MONGO_URI="$(MONGO_ADDR="$mongo_addr" uv run --frozen python -c '
import os
from urllib.parse import quote_plus
user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
print(f"mongodb://{user}:{password}@{os.environ["MONGO_ADDR"]}/?authSource=admin&directConnection=true")
')"
export MONGO_URI
export CONFIG_DIR="$PWD/config"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://${otel_addr}"

# No model can be called: the worker runs the stub executor and every alias goes to the fake connector.
export LLM_CONNECTOR=fake
export LLM_MODEL_FAST="${LLM_MODEL_FAST:-fake}" LLM_MODEL_STANDARD="${LLM_MODEL_STANDARD:-fake}"
export LLM_MODEL_DEEP="${LLM_MODEL_DEEP:-fake}"
unset ORCHESTRATOR_EXECUTOR
JWT_SECRET="$(openssl rand -hex 32)"
TELEGRAM_WEBHOOK_SECRET="$(openssl rand -hex 32)"
export JWT_SECRET TELEGRAM_WEBHOOK_SECRET
# The chat routes are built only with both set (CHAT-1); only the history read (`GET /runs/{id}/chat`) is
# measured, which reads Mongo. The hub address is a closed loopback port: nothing here asks the assistant.
AGENT_TOKEN_KEY="$(openssl rand -hex 32)"
export AGENT_TOKEN_KEY MCP_HUB_URL="http://127.0.0.1:9/mcp"

uv run --frozen uvicorn onb_api.main:app --host 127.0.0.1 --port "$PERF_API_PORT" --no-access-log \
  >"$LOG_DIR/api.log" 2>&1 &
pids+=($!)
uv run --frozen python -m onb_orchestrator >"$LOG_DIR/worker.log" 2>&1 &
pids+=($!)

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${PERF_API_PORT}/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -fsS "http://127.0.0.1:${PERF_API_PORT}/health" >/dev/null || {
  echo "perf: the API did not start; see $LOG_DIR/api.log" >&2
  exit 1
}

# Caddy's dev certificate comes from its local CA: verify against that root, never skip verification.
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$LOG_DIR/caddy-root.crt" >/dev/null
uv run --frozen python tools/perf/latency.py --base-url "https://localhost:${CADDY_HTTPS_PORT}/api" \
  --ca-file "$LOG_DIR/caddy-root.crt" "$@"
