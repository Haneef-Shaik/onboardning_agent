# Handoff — F-2 · Run the base stack in Docker Compose        attempt 1 · goal/f-2-compose-stack · see `git log -1` (evidence captured at c4abe53)

**Outcome claimed.** `make up` builds and starts MongoDB 8.0 (single-member replica set `rs0`, auth,
keyfile, named volume), the OTel collector, Jaeger v2 and Caddy, and waits until all four are
healthy. Mongo, the collector and Jaeger sit only on an internal network. Caddy is the only service
that publishes ports, and it binds to 127.0.0.1 by default.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| After `make up`, `docker compose ps` shows every service healthy (output recorded) | yes | `docs/evidence/F-2/compose-ps-healthy.txt`: a cold start after `docker compose down -v`. `make up` exit=0, and `caddy/jaeger/mongo/otel-collector running healthy`. Guarded live by `test_every_service_is_running_and_healthy` and `test_every_service_has_a_healthcheck_and_restarts`. |
| An integration test commits a multi-document transaction against the compose Mongo (`make test-int` result line) | yes | `docs/evidence/F-2/make-test-int.txt`: `make test-int` exit=0 with `12 passed` (stack) and `4 passed` (py-data), then `NX Successfully ran target test-int for project py-data`. The transaction test is `libs/py-data/tests_int/test_compose_mongo_transaction.py::test_multi_document_transaction_commits`: two collections in one transaction, both committed. `test_aborted_transaction_writes_nothing` shows the transaction is real, since an aborted one leaves both collections empty. |
| `docker compose config` shows a `ports:` mapping only on `caddy` | yes | `docs/evidence/F-2/compose-config-ports.txt`: one `ports:` key, at line 30 inside `caddy:` (lines 1–55). The JSON view shows `caddy: [127.0.0.1:18082->80, 127.0.0.1:18445->443]`, and `jaeger/mongo/otel-collector: ports=[]`. Guarded by `test_only_caddy_publishes_ports`. |
| `scripts/check_env_example.py` (run by `make check`) fails when a variable used in compose is missing from `.env.example` (seen once) | yes | `docs/evidence/F-2/env-example-check-fails.txt`: with `CADDY_HTTP_PORT` deleted from `.env.example`, `make check` prints `check_env_example: .env.example does not document CADDY_HTTP_PORT` and then `make: *** [env-check] Error 1`, exit=2. After restoring it, `make env-check` exits 0. `check: plan-check env-check …` is guarded by `test_make_check_runs_env_example_check`. |
| An unauthenticated `mongosh` connection is refused (output recorded) | yes | `docs/evidence/F-2/mongosh-unauthenticated-refused.txt`: without credentials, `MongoServerError: Command listDatabases requires authentication` exit=1 and `replSetGetStatus requires authentication` exit=1. A wrong password gives `Authentication failed.` exit=1. On the host, `nc -z 127.0.0.1 27017` gives exit=1 (no port). The control with credentials returns `1`. Guarded by `test_unauthenticated_mongosh_is_refused` and, through the tunnel with pymongo, by `test_unauthenticated_client_is_refused` (code 13). |

`make check` at c4abe53: `docs/evidence/F-2/make-check.txt` ends `make check: all passed` exit=0.
Tooling has `152 passed, 67 subtests passed`, and lint, typecheck and test pass for 17/17 projects.

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `cp .env.example .env`, fill in the empty values, then `make up`. After that, plain `docker compose …` works from the repo root, because `.env` sets `COMPOSE_FILE`, `COMPOSE_PROJECT_NAME` and `COMPOSE_PROFILES=dev`. | `compose-ps-healthy.txt`, `.env.example` |
| Inside the compose network, Mongo is `mongo:27017`, replica set `rs0`, root user `MONGO_ROOT_USERNAME`/`MONGO_ROOT_PASSWORD`, `authSource=admin`. Transactions work. | `test_server_is_a_replica_set_primary`, `test_multi_document_transaction_commits` |
| OTLP endpoints on the network are `otel-collector:4317` (gRPC) and `otel-collector:4318` (HTTP). Traces arrive in Jaeger (query API `/api/v3/services`). | `test_collector_forwards_traces_to_jaeger` |
| Jaeger UI is at `https://jaeger.localhost:<CADDY_HTTPS_PORT>` behind basic auth, and is not served over HTTP. | `test_jaeger_through_caddy_requires_basic_auth`, `test_jaeger_is_not_served_over_plain_http` |
| `make test-int` runs `make up`, sources `.env`, runs `tools/tests_int`, then `nx run-many -t test-int`. A project joins the gate by adding a `test-int` target (see `libs/py-data/project.json`). | `Makefile`, `make-test-int.txt` |
| Python integration tests can use the `root_client`, `anonymous_client` and `mongo_tunnel` fixtures pattern in `libs/py-data/tests_int/conftest.py`. The tunnel is a socat container on 127.0.0.1 at a random port, labelled per project, and stale copies are removed. | `make-test-int.txt`, `mutation-checks.txt` (0 leftover tunnels) |
| Data survives `make down` + `make up` (named volumes `mongo_data`, `mongo_config`). | `docs/evidence/F-2/mongo-volume-persists.txt`, `test_mongo_data_is_on_a_named_volume` |
| Every pulled image is pinned by version and digest; the collector is a local build of the pinned binary (`infra/otel/Dockerfile`). | `test_pulled_images_are_pinned_by_version_and_digest` |
| New compose variables must go in `.env.example`, or `make check` fails. | box 4 evidence |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show F-2` lists no ledger rows for F-2 | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/F-2/make-check.txt`) |
| Integration | `make test-int` | exit=0. Stack checks `12 passed`, and py-data `4 passed` (`make-test-int.txt`) |

The other gates (test-xt, contracts-check, evals-selftest, test-pii, e2e) do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-1 | `make test-int` fans out `nx run-many -t test-int` | yes. py-data's new target was picked up (`Successfully ran target test-int for project py-data`) |
| F-1 | Workspace deps go in the member's `pyproject.toml`, then `uv lock` | yes. pymongo was added to `libs/py-data/pyproject.toml`, and `uv.lock` was updated |
| F-1 | Use `node_modules/.bin/nx`; the rtk hook rewrites some commands | yes. The rtk hook also truncates `make` output written to files, so evidence was captured with `rtk proxy make …` |

## Mutation checks
Full output is in `docs/evidence/F-2/mutation-checks.txt` (at c4abe53). Every file was restored with
"0 files differ". After the run all four services were healthy again, with 0 leftover tunnels.
- M1: mongo given `ports: ["127.0.0.1:27017:27017"]`. `test_only_caddy_publishes_ports` FAILED.
- M2: jaeger healthcheck disabled. `test_every_service_has_a_healthcheck_and_restarts` FAILED.
- M3: caddy `restart: "no"`. The same test FAILED.
- M4: backend network `internal: false`. `test_internal_services_are_only_on_the_private_network` FAILED.
- M5: `mongo:8` with no digest. `test_pulled_images_are_pinned_by_version_and_digest` FAILED.
- M6: `env-check` dropped from `check:`. `test_make_check_runs_env_example_check` FAILED.
- M7: the checker ignores bare `$VAR`. `test_finds_interpolated_variables[cmd: echo $PLAIN_VAR…]` FAILED.
- M8: the checker stops honouring `$$`. `test_finds_interpolated_variables[…$$NOT_A_VAR…]` and `test_repo_env_example_covers_repo_compose` FAILED.
- M9: the checker skips `#` lines. `test_finds_interpolated_variables[cmd: |\n  # ${IN_BLOCK_SCALAR}…]` FAILED.
- M10: `basic_auth` removed (live, caddy recreated). `test_jaeger_through_caddy_requires_basic_auth` and `test_dev_caddyfile_protects_jaeger_with_basic_auth` FAILED.
- M11: Jaeger served on `http://` again (live). `test_jaeger_is_not_served_over_plain_http` and `test_jaeger_credentials_never_travel_over_plain_http` FAILED.
- M12: no `chmod 400` for an existing keyfile. `test_gen_keyfile_keeps_an_existing_key_but_repairs_its_mode` FAILED.
- M13: tunnel image without digest. `test_test_tunnel_image_is_pinned_by_digest` FAILED.
- M14: mongod without `--replSet` (live). `test_server_is_a_replica_set_primary`, `test_multi_document_transaction_commits`, `test_aborted_transaction_writes_nothing` and `test_mongo_runs_a_replica_set_with_auth_and_keyfile` FAILED.
- M15: mongod with `--noauth` and no keyfile (live). `test_unauthenticated_mongosh_is_refused` and `test_mongo_runs_a_replica_set_with_auth_and_keyfile` FAILED. `test_unauthenticated_client_is_refused` ERRORED in its fixture (Mongo was not reachable as an auth-less replica set), so it did not pass but did not reach its assertion either.
- Box 4 product guard: seen to fail in `make check` (above).

RED before GREEN: `docs/evidence/F-2/red-static-tests.txt` (19 failed and 13 errors before the stack
files existed) and `docs/evidence/F-2/red-review-fixes.txt` (7 failed before the review fixes). The
live tests in `tools/tests_int` and `libs/py-data/tests_int` were written before the stack. Their
first run failed `test_collector_forwards_traces_to_jaeger` because of the Jaeger v2 API path, which
was fixed in the test after confirming that spans did arrive.

## Review findings left open
Code review (general-purpose agent with a reviewer brief) and security review both found no critical
or high issues. These were fixed: digest pins (including the tunnel image), Jaeger over HTTPS only,
a catch-all 404, a tunnel label scoped per project with stale cleanup, a tunnel that targets the
Mongo IP instead of relying on DNS, the keyfile mode repair and empty-file replacement, `#` lines
scanned by the checker, both `compose ps` JSON shapes, curl with `--noproxy` and the bind address,
and docs on parallel stacks, quoting and Engine 25+.

Still open:
- low · `infra/docker-compose.yml:26-27,37` · The root password is in the container env (`docker inspect`). It is also on mongosh's argv in the healthcheck and in one live test, where `ps` can see it. Left because this is a local dev stack, and anyone who can read it already controls the Docker socket. Fix with DEP-1 or T-1: `MONGO_INITDB_ROOT_PASSWORD_FILE` plus a compose secret, with the healthcheck reading the file.
- low · `.env.example` · The default usernames are `root` and `admin`, and there is no minimum password length. Left because compose already refuses empty values, and a strength policy belongs with the VM deployment.
- low · `.env.example` · Two stacks on one machine with the same `COMPOSE_PROJECT_NAME` take over each other's containers. This is documented, not enforced.
- low · `infra/docker-compose.yml` · The `onb/otel-collector:0.165.0` tag is global across compose projects. That is harmless while the Dockerfile is identical everywhere.
- low · The integration tests have been run only on macOS Docker Desktop (Engine 29.8.2). The tunnel now avoids the DNS dependency that differs on Linux, but the first Linux run is F-3's CI.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. I worked to the text from `plan.py goal F-2`.
- No `vm` Caddyfile or TLS for real hosts. That is the deployment goal. The base services carry `profiles: [dev, vm]` so the VM profile can reuse them.
- Testcontainers (named in MASTER-PLAN §7's integration-gate description) is not used. Box 2 requires the compose Mongo, so tests use the compose stack through `make test-int: up`.

## Found for other goals
- **F-3 (CI):** `make test-int` needs Docker Engine 25+ and a `.env`. CI should write one from `.env.example` with generated secrets (see `.env.example` for how to make the Caddy hash) and pick free Caddy ports. It also needs `curl` on the runner.
- **F-4 (OTel):** send OTLP to `otel-collector:4317`/`4318`. Jaeger v2's query API is `/api/v3/...`. The collector 0.165 exporter is `otlp_grpc`, because `otlp` is deprecated.
- **T-1 / py-data:** pymongo 4.19 is now a py-data dependency. Transactions need a session against `rs0`; in-network apps can use `mongodb://…@mongo:27017/?replicaSet=rs0&authSource=admin`.
- **DEP-1:** the VM Caddyfile, `CADDY_BIND_ADDRESS=0.0.0.0` only with real TLS, the password-file secret (open finding above), and the nightly `mongodump` (PRD §7.11).
- **MASTER-PLAN §7:** the integration gate's description says "testcontainers Mongo". F-2 uses the compose Mongo instead. Consider rewording it.

## Decisions forced (ADRs written)
None needed an ADR. The choices are recorded in `infra/README.md` and in comments next to each one:
- A socat tunnel for host-side tests, because Mongo is never published.
- An alpine rebuild of the collector, because it needs a healthcheck.
- The replica set is initiated from the healthcheck, because the image's first-boot init runs mongod without `--replSet`.

## Traps hit
- The official mongo entrypoint drops `--replSet` and `--keyFile` during first-boot user creation, so `rs.initiate` cannot go in `/docker-entrypoint-initdb.d`. It runs from the healthcheck script instead.
- The keyfile bind-mounted from macOS is owned by the host user. mongod needs it owned by `mongodb` with mode 0400, so the entrypoint `install`s a copy.
- The collector image is distroless, so it has no shell for a healthcheck.
- The rtk shell hook truncates `make` output even when it is redirected to a file. A first `make-check.txt` held "… (409 lines truncated)". Use `rtk proxy`.
- Docker Desktop was not running at session start. I started it with `open -a Docker` and did not change the docker context.
