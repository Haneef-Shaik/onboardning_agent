#!/usr/bin/env bash
# Start the e2e API and the Vite dev server on the host, then curl them through this worktree's Caddy.
set -u
export E2E_API_PORT=18731 E2E_PASSWORD="$(openssl rand -hex 12)" E2E_JWT_SECRET="$(openssl rand -hex 32)"
uv run --frozen python apps/web/e2e/serve_api.py >/dev/null 2>&1 &
API=$!
WEB_PORT=18732 WEB_API_TARGET=http://127.0.0.1:18731 node_modules/.bin/vite --config apps/web/vite.config.ts >/dev/null 2>&1 &
WEB=$!
trap 'kill $API $WEB 2>/dev/null' EXIT
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://127.0.0.1:18731/health && curl -sf -o /dev/null http://127.0.0.1:18732/ && break
  sleep 0.5
done
echo '$ curl -sk https://localhost:19801/api/health'
curl -sk https://localhost:19801/api/health; echo
echo '$ curl -sk -o /dev/null -w "%{http_code}" https://localhost:19801/api/me   # no token'
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:19801/api/me
echo '$ curl -sk https://localhost:19801/ | grep -o "<title>.*</title>\|/src/main.tsx"'
curl -sk https://localhost:19801/ | grep -o '<title>.*</title>\|/src/main.tsx'
echo '$ curl -sk -o /dev/null -w "%{http_code}" https://localhost:19801/healthz'
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:19801/healthz
echo '$ curl -s -o /dev/null -w "%{http_code}" http://localhost:19800/api/health   # plain HTTP: not served'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:19800/api/health
echo '$ curl -s -o /dev/null -w "%{http_code}" http://localhost:19800/healthz'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:19800/healthz
