# infra

The base Docker Compose stack (F-2; PRD §7.11). Needs Docker Engine 25+ and Compose v2.

| Path | What it is |
|------|------------|
| `docker-compose.yml` | mongo (replica set `rs0`, auth, keyfile, named volume), otel-collector, jaeger, caddy. Only caddy publishes ports; the rest sit on the internal `backend` network. |
| `mongo/rs-init.js` | Run by the mongo healthcheck: `rs.initiate` on first boot, healthy only on a writable primary. |
| `mongo/gen-keyfile.sh` | Writes `mongo/keyfile` (git-ignored, mode 0400). Run by `make up`. |
| `otel/Dockerfile`, `otel/collector.yaml` | The pinned collector binary on alpine (so it can be healthchecked); OTLP in, Jaeger out. |
| `caddy/Caddyfile.dev` | `localhost` health endpoint; Jaeger UI at `https://jaeger.localhost` behind basic auth. |

```bash
cp .env.example .env    # fill in the empty values
make up                 # build, start, wait until every service is healthy
make test-int           # stack checks + integration tests against the compose services
make logs
make down               # data volumes are kept; `docker compose down -v` wipes them
```

Mongo is never published. Integration tests reach it through a short-lived socat tunnel on
`127.0.0.1` (`libs/py-data/tests_int/conftest.py`).
