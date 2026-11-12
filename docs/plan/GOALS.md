# Onboarding Platform MVP: every goal

> **Source:** [PRD v0.3](../PRD.md), 10 November 2026. The PRD owns requirements; this file owns
> sequencing and the definition of done. The rules for running, proving and declaring a goal are in
> [MASTER-PLAN.md](../MASTER-PLAN.md).

**This file is the source of the goal board.** Each `### ID · Title` card becomes one node. Statuses
live on the board (the claude.ai artifact named in [README.md](README.md)), never in this file. After
changing a card, run `python3 scripts/plan/plan.py build`. The build refuses a card without a
contract, a need that doesn't exist, a cycle, a PRD functional requirement (`FR-*`) that no card
names, and a ledger row in [ACCEPTANCE.md](ACCEPTANCE.md) whose gate doesn't wait on its goals.

Run a goal with `/run-goal <ID>`. That skill hands Claude Code a `/goal` condition generated from the
card (`python3 scripts/plan/plan.py goal <ID>`), so the session keeps working until every box is
proven. See [SESSION-PROMPT.md](SESSION-PROMPT.md).

```json
{
  "title": "Onboarding Platform MVP",
  "owner": "Haneef",
  "people": [
    {"name": "Haneef", "colour": "#1f6feb"}
  ],
  "tracks": {
    "M0": "Foundations",
    "M1": "Core platform: auth, tenancy, PII vault",
    "M2": "Mongo MCP and the hub",
    "M3": "LLM connectors, templates, evals",
    "M4": "Runtime: event bus, orchestrator, approvals",
    "M5": "Frontend",
    "M6": "Hardening, deployment, demo"
  },
  "config": {
    "subtitle": "From docs/plan/GOALS.md (PRD v0.3, 10 Nov 2026). One developer, one lane. Columns are the earliest step a goal can start; work top-down within a column.",
    "milestone": "MS-6",
    "milestoneLabel": "MVP complete",
    "sizes": {
      "S": "one short /goal session (half a day or less)",
      "M": "one full /goal session (about a day)",
      "L": "two or three sessions; the checkpoint carries you between them"
    },
    "firstStepLabel": "Step 1",
    "stepLabel": "Step",
    "docPrefix": "docs/plan/",
    "workflowHint": "Pick the top Ready goal and run /run-goal <ID>. A goal is Done only after goal-verifier passes every Done-when box and the branch is merged to main; a milestone only after its Exit checks pass.",
    "refPlaceholder": "branch or commit, e.g. goal/t-1-tenant-repo or 1a2b3c4",
    "hubs": [
      {"key": "F", "ids": ["F-1", "F-4"], "text": "F = F-1 + F-4, the monorepo and telemetry foundation"},
      {"key": "P", "ids": ["T-1", "AUTH-1", "AUD-1"], "text": "P = T-1 + AUTH-1 + AUD-1, tenancy, auth and audit"},
      {"key": "E", "ids": ["EVAL-1"], "text": "E = EVAL-1, the eval harness"}
    ],
    "slug": "onboarding-mvp-goals"
  }
}
```

## How to read a card

- **Needs** are goals that must be **Done** (merged to `main`) first. Ready and blocked are worked
  out from them on the board.
- **Size**: S = a short session, M = one full session, L = two or three sessions.
- **Milestone** is the PRD §14 milestone the goal ships in. `MS-*` cards are the milestone gates.
- **PRD** names the sections, functional requirements (`FR-*`), user stories (`U*`) and
  non-functional targets the goal must satisfy. If a card and the PRD disagree, the PRD wins and the
  card is corrected.
- **Done when** is the contract. Every box needs evidence a verifier can check
  ([MASTER-PLAN §7](../MASTER-PLAN.md#7-evidence-and-gates)). One unmet box means not Done.
- **Exit checks** is a milestone's contract.
- **Traps** are the specific ways the goal goes wrong, written before it does.

Terms used in the boxes: **the standard gates**, **evidence folder** (`docs/evidence/<ID>/`) and
**meets the eval gates** (safety 100% pass^3, capability ≥ 90% pass@1 and ≥ 80% pass^3) are defined
in [MASTER-PLAN §7](../MASTER-PLAN.md#7-evidence-and-gates).

---

## M0 · Foundations

### F-1 · Create the Nx monorepo with every Python and web project
- **Size:** M · **Milestone:** M0 · **Label:** Nx monorepo
- **Needs:** —

**Outcome.** One Nx workspace where every app and lib in the PRD §11 layout exists as a project with
lint, typecheck and test targets, and `make check` runs all of them.

**PRD.** §11 (tech stack, repo layout), §12 Quality.

**Scope.**
1. `git init` on `main`. `.gitignore` covers `.env`, `evals/reports/`, `.venv`, `node_modules`,
   build output and Mongo keyfiles. Existing `docs/` and `evals/` stay untouched.
2. Nx + `@nxlv/python` + uv, Python 3.12, one root uv workspace so libs import each other without
   publishing.
3. Projects: `apps/{api,orchestrator,mcp-hub,mcp-mongo,mcp-mocks,web}` and
   `libs/{py-contracts,py-data,py-crypto,py-llm,py-agents,py-guardrails,py-telemetry,py-eventbus,py-evals,ts-ui,ts-api-client}`,
   each with one placeholder test. Folders `config/`, `templates/`, `infra/`, `docs/adr/`.
4. Python: ruff, mypy (strict), pytest + coverage. Web: eslint, tsc, Vitest.
5. `Makefile`: `bootstrap`, `check` (lint, typecheck, unit tests, coverage, plan check), `test-int`,
   `fmt`.

**Done when.**
- [ ] From a fresh clone, `make bootstrap && make check` exits 0 (command and last lines in the handoff)
- [ ] `npx nx show projects` lists all 17 projects of the PRD §11 layout
- [ ] An 80% line-coverage threshold is enforced for every `libs/py-*` project, seen to fail once when a lib drops below it (output recorded)
- [ ] `make check` runs `python3 scripts/plan/plan.py check` and fails when GOALS.md has a broken need (seen once)
- [ ] `git check-ignore .env evals/reports/x.json` prints both paths

**Traps.** `@nxlv/python` defaults to Poetry; pin it to uv. Don't let each lib get its own virtualenv.

### F-2 · Run the base stack in Docker Compose
- **Size:** M · **Milestone:** M0 · **Label:** compose stack
- **Needs:** F-1

**Outcome.** `make up` starts MongoDB (replica set, auth, keyfile, persistent volume), the OTel
collector, Jaeger and Caddy on a private network, and only Caddy publishes ports.

**PRD.** §7.11, §7.7, §7.2 (replica set for transactions).

**Scope.** `infra/docker-compose.yml` with a `dev` profile; Mongo init script (`rs.initiate`, root
user from env); a keyfile generator script (the keyfile is never committed); collector config to
Jaeger; a dev `Caddyfile`; healthchecks and `restart: unless-stopped`; `.env.example` documenting
every variable; `make up`, `make down`, `make logs`.

**Done when.**
- [ ] After `make up`, `docker compose ps` shows every service healthy (output recorded)
- [ ] An integration test commits a multi-document transaction against the compose Mongo (`make test-int` result line)
- [ ] `docker compose config` shows a `ports:` mapping only on `caddy`
- [ ] `scripts/check_env_example.py` (run by `make check`) fails when a variable used in compose is missing from `.env.example` (seen once)
- [ ] An unauthenticated `mongosh` connection is refused (output recorded)

### F-3 · Gate every change in CI
- **Size:** S · **Milestone:** M0 · **Label:** CI gate
- **Needs:** F-1

**Outcome.** Every push runs `make check` and the integration tests in CI, and a red run blocks
merging into `main`.

**PRD.** §12 Quality, §8.2 (CI runs the eval self-test once it exists).

**Scope.** Choose the CI host and record it in `docs/adr/0009-ci-host.md`. Workflow: bootstrap with
caches, `make check`, then an integration job with Docker for testcontainers. Pushing to a remote is
your call; the goal stops at "workflow ready" if no remote exists yet and says so.

**Done when.**
- [ ] `docs/adr/0009-ci-host.md` records the host and why (closes DEC-5)
- [ ] A CI run with a deliberately failing test is red, and the fixed run is green (both run links in the handoff)
- [ ] The integration job starts a testcontainers Mongo and passes (run link)

### F-4 · Trace every Python service with OpenTelemetry
- **Size:** M · **Milestone:** M0 · **Label:** telemetry lib
- **Needs:** F-2

**Outcome.** `libs/py-telemetry` gives every service a one-call OTel setup and helpers to store a
`traceparent` on a record and continue the trace later, so async waits stay in the run's trace.

**PRD.** §5.7, §7.7.

**Scope.** `init_telemetry(service_name)` with env-driven OTLP export and auto-instrumentation for
FastAPI, PyMongo and httpx; span attribute helpers (`org_id`, `run_id`, `task_id`, `agent.name`,
`tool.name`); `inject_traceparent()` / `continue_from(traceparent)`; a span-processor hook where
OBS-1's redaction processor plugs in; content capture off by default; an in-memory exporter fixture
for tests.

**Done when.**
- [ ] A unit test shows a span continued from a stored `traceparent` carries the original trace id
- [ ] A test FastAPI app's span is returned by Jaeger's API through the compose collector (`curl` output recorded)
- [ ] A test shows a registered processor sees every span before export
- [ ] `libs/py-telemetry` coverage ≥ 80%

### MS-0 · Foundations ready
- **Size:** S · **Milestone:** M0 · **Label:** M0 gate
- **Needs:** F-1, F-2, F-3, F-4

**Outcome.** A clean, green, traced base that every later goal branches from.

**PRD.** §14 M0.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] From a fresh clone: `make bootstrap && make up && make check && make test-int` pass (output in `docs/evidence/MS-0/`)
- [ ] CI is green on `main` (run link), or F-3's handoff records that no remote exists yet
- [ ] The DEC-5 row in ACCEPTANCE.md is `pass`

---

## M1 · Core platform: auth, tenancy, PII vault

### C-1 · Stand up the API with one envelope and a generated TS client
- **Size:** M · **Milestone:** M1 · **Label:** API + contracts
- **Needs:** F-1, F-4

**Outcome.** `apps/api` (FastAPI) answers every request in the `{success, data, error, meta}`
envelope from `libs/py-contracts`, and `libs/ts-api-client` is generated from its OpenAPI with a
drift check.

**PRD.** §10 (envelope, `org_id` never an input), §11 Contracts.

**Scope.** Envelope and error-code models, page meta, exception handlers, `/health`; telemetry
initialised; OpenAPI export script; TS client generation; `make contracts-check`.

**Done when.**
- [ ] A test walks every request model and fails if one declares `org_id` (seen to fail once)
- [ ] `make contracts-check` fails after a model change without regeneration and passes after it (both outputs)
- [ ] `apps/web` imports `ts-api-client` and typechecks
- [ ] An unhandled exception returns the envelope with a stable error code and no stack trace (test)

### T-1 · Make tenant scoping impossible to forget
- **Size:** M · **Milestone:** M1 · **Label:** tenant repository
- **Needs:** F-2

**Outcome.** `libs/py-data` is the only path to MongoDB: a `TenantRepository` that adds `org_id` to
every filter, insert and pipeline, and raises when there is no tenant context.

**PRD.** §5.4, §7.9 (Request context, Data access, Indexes, IDs).

**Scope.** `TenantContext` contextvar and `tenant_scope(org_id)`; async PyMongo client factory;
`TenantRepository` (find, by-id lookups as `{_id, org_id}`, insert setting `org_id`, update that
forbids `$set` of `org_id` and sets it on upsert, aggregate with `$match` prepended, count); a
`NotFound` for foreign ids; an index helper requiring an `org_id` prefix; an explicit, short list of
global collections (`orgs`, `agent_registry`, `tool_catalog`).

**Done when.**
- [ ] Every repository operation raises without a context, and with one sees only its org (two-org test against testcontainers Mongo)
- [ ] A foreign `_id` lookup raises `NotFound`, not a permission error (test)
- [ ] Mutation check: removing the `$match` prepend fails a named test (output recorded)
- [ ] A test asserts `py-data` exports no `Collection` or `Database` object
- [ ] `libs/py-data` coverage ≥ 80%

**Traps.** `$match` must be first even when the caller passes `$sort` first. An upsert must set
`org_id`.

### T-2 · Catch any unscoped query at runtime
- **Size:** S · **Milestone:** M1 · **Label:** query safety net
- **Needs:** T-1

**Outcome.** A PyMongo command listener (tests and staging) fails any command on a tenant collection
whose filter lacks `org_id`, and every tenant index starts with `org_id`.

**PRD.** §7.9 (Safety net, Indexes).

**Done when.**
- [ ] A raw unscoped `find` through a deliberately leaked client trips the listener (test)
- [ ] The listener is on for every integration test, asserted by a fixture test
- [ ] An index check in `make test-int` fails on a tenant index not prefixed with `org_id` (seen once)

### AUTH-1 · Log in with a JWT that carries org and role
- **Size:** M · **Milestone:** M1 · **Label:** JWT + roles
- **Needs:** C-1, T-1

**Outcome.** HR_ADMIN, HR and MANAGER users log in with a password and get an HS256 JWT
`{sub, org_id, role, exp}`; every route takes the org from the token and enforces the role.

**PRD.** §3, §7.8, §7.9 (Identity, Request context).

**Scope.** `users` collection, bcrypt, `POST /auth/login`, `GET /me`; a dependency that sets
`TenantContext` from the JWT (missing or invalid → 401); `require_role(...)`; a manager-scope helper
(`manager_id == sub`); `JWT_SECRET` from env with fail-fast; no refresh tokens.

**Done when.**
- [ ] Wrong password, expired token, tampered token and missing token each return 401 (named tests)
- [ ] A test proves an `org_id` in body, query or path is rejected or ignored
- [ ] A role-matrix test covers each role × each guarded route with the expected 200/403
- [ ] Startup fails without `JWT_SECRET` (test)

### AUD-1 · Record every privileged action in an append-only audit log
- **Size:** S · **Milestone:** M1 · **Label:** audit log
- **Needs:** T-1, AUTH-1

**Outcome.** One `audit(...)` call writes an append-only, org-scoped record that later goals use for
denials, reveals, decisions and manual overrides; HR_ADMIN reads it at `GET /audit`.

**PRD.** §7.6 Audit, §10 Registry & audit, U7.

**Scope.** Record `{org_id, actor: user|agent, action, target, outcome, reason, trace_id, at}`;
insert-only repository; `GET /audit` with filters and pagination.

**Done when.**
- [ ] The audit repository has no update or delete path (test)
- [ ] HR and MANAGER get 403 on `GET /audit`; HR_ADMIN gets 200 (test)
- [ ] A record carries the current span's trace id (test)

### T-3 · Prove no actor can reach another org
- **Size:** M · **Milestone:** M1 · **Label:** cross-tenant suite
- **Needs:** AUTH-1, T-2, AUD-1

**Outcome.** A CI-blocking suite seeds two orgs and, for every API route, asserts an org-A actor
cannot read, list, update or infer org-B data. A coverage check fails when a route has no case.

**PRD.** §7.9 Testing, §12 Tenant isolation.

**Scope.** Two-org fixture (reuse the orgs in `evals/fixtures/seed_v1.json`); a route enumerator over
the FastAPI app; a per-route case table; `make test-xt`; extension points that HUB-1 (MCP tools) and
EVT-1 (event consumers) fill in.

**Done when.**
- [ ] `make test-xt` passes and lists every current route
- [ ] Adding a dummy route without a case makes `make test-xt` fail (seen once, output recorded)
- [ ] Counts and list totals never include the other org (test)
- [ ] `make check` or CI runs `make test-xt` as a required step

**Traps.** Inference leaks: list totals, 403-vs-404 differences and timing on unique-index errors.

### PII-1 · Give each org its own keys, wrapped by the master key
- **Size:** M · **Milestone:** M1 · **Label:** per-org keys
- **Needs:** T-1

**Outcome.** Each org has a wrapped data key, a separate signing key and a separate Aadhaar key in
`org_keys`, unwrapped only in memory with `PII_MASTER_KEY` and versioned for rotation.

**PRD.** §7.10 (Encryption, Integrity, Aadhaar), §7.9 Encryption, §15 key loss.

**Scope.** `libs/py-crypto` keyring: generate, wrap, unwrap, `key_version`, rotate (old versions
stay for decryption); fail-fast when `PII_MASTER_KEY` is missing or short; `docs/runbooks/key-rotation.md`.

**Done when.**
- [ ] Org A's key cannot decrypt org B's data; a wrong master key fails closed (tests)
- [ ] After rotation, old ciphertext still decrypts and new writes use the new version (test)
- [ ] No key material appears in logs (caplog test)
- [ ] The rotation runbook exists and its steps are exercised by the rotation test
- [ ] `libs/py-crypto` coverage ≥ 80%

### PII-3 · Validate, detect, mask and blind-index sensitive values
- **Size:** M · **Milestone:** M1 · **Label:** PII detectors + masks
- **Needs:** F-1

**Outcome.** Pure functions for each sensitive field (validators, free-text detectors, display masks,
HMAC blind indexes) shared by the API, guardrails, telemetry and evals.

**PRD.** §7.10 (Lookup/dedupe, Aadhaar validation, Display, Free-text input), FR-P1.

**Scope.** Aadhaar (12 digits, not starting 0/1, Verhoeff), PAN, IFSC, Indian mobile, email, bank
account. Detectors return typed spans; `redact()` replaces them with `[PAN]`-style markers. Masks
exactly as the PRD. Blind index = HMAC-SHA256 with a per-org key. Detectors in
`libs/py-guardrails/pii`, masks and blind index in `libs/py-crypto`.

**Done when.**
- [ ] Each PRD mask example produces exactly the PRD string (table test)
- [ ] Verhoeff valid and invalid vectors pass; Aadhaar starting with 0 or 1 is rejected
- [ ] A corpus of 50+ synthetic positives and negatives has zero false negatives; the false-positive rate is recorded in the handoff
- [ ] A property test shows `redact()` output contains no detector hit

**Traps.** A 12-digit number that fails Verhoeff isn't Aadhaar (order ids). The mobile regex can
match inside a 12-digit Aadhaar.

### PII-2 · Store sensitive values only in a signed, encrypted vault
- **Size:** L · **Milestone:** M1 · **Label:** PII vault
- **Needs:** PII-1, PII-3

**Outcome.** The vault writes AES-256-GCM ciphertext with AAD binding and an HMAC record signature,
returns an opaque `ptk_` token, and every read verifies the signature and fails closed.

**PRD.** §5.5, §7.10 (Principle, Vault record, Tokens, Encryption, Integrity, Writers, Aadhaar,
Retention status), FR-P4.

**Scope.** Vault record exactly as the PRD; token = `ptk_` + ULID + 128 random bits; AAD =
`org_id|subject.id|field|token|version`; `sig` over every field but itself; `put` (new version + new
token, old one `superseded`); `resolve(token, expected org/subject/field)`; `purged` resolves to
`[purged]`; Aadhaar in `pii_vault_aadhaar` with the Aadhaar key and a consent record. Only the API
layer gets a `Vault`; agents never do.

**Done when.**
- [ ] Each tamper fails closed with its own named test: changed `subject.id`, ciphertext swapped between records, changed `field`, changed `status`, edited `sig`
- [ ] An update creates a new token, marks the old record `superseded`, and both still verify (test)
- [ ] Aadhaar records land only in `pii_vault_aadhaar` and use a different key from other fields (test)
- [ ] Tokens have the documented prefix and length and use `secrets` for randomness (test)
- [ ] `libs/py-crypto` coverage stays ≥ 80%

### PII-4 · Let only orchestrator-signed grants resolve a token at a sink
- **Size:** M · **Milestone:** M1 · **Label:** signed grants
- **Needs:** PII-2

**Outcome.** A grant library issues JWS grants `{org_id, run_id, task_id, subject_id, fields, sink,
exp ≤ 10 min, jti}` and a sink-side verifier that checks the grant, the token binding and the vault
signature before releasing a value; the field → sink policy lives in config.

**PRD.** §7.10 Signed grants, FR-P5, §8.1 (Tool: AI reading, inventing, swapping or altering PII).

**Scope.** `config/pii_sink_policy.yaml` (`mobile → notify.telegram`, `email → notify.email`;
Aadhaar, PAN, bank → none); `issue_grant` (orchestrator key from env); `resolve_at_sink(grant,
sink_id, tokens)`; `jti` replay protection until expiry; every resolution writes `pii_access_log`;
failures raise `GrantRejected` with a reason and emit an audit record.

**Done when.**
- [ ] Each rejection has a named test: bad signature, expired, wrong sink, another hire's token, another org's token, field not granted, policy-forbidden field (Aadhaar) even when the grant lists it, replayed `jti`
- [ ] A successful resolution writes exactly one `pii_access_log` entry holding a masked value only (test)
- [ ] Mutation check on the subject-binding check (output recorded)

### HIRE-1 · Create hires with PII held as tokens, and reveal with a reason
- **Size:** L · **Milestone:** M1 · **Label:** hires + reveal
- **Needs:** AUTH-1, AUD-1, PII-2, PII-3, T-3

**Outcome.** HR creates a hire whose sensitive fields go straight into the vault; `new_hires` keeps
only tokens and blind indexes; HR reveals one field with a reason (Aadhaar: HR_ADMIN only), and every
reveal is logged.

**PRD.** FR-R1 (hire part), FR-P1, FR-P2, FR-P4, FR-P5, §7.10 (Reveal, Aadhaar, Lookup), §10 Hires, U3.

**Scope.** `POST /hires`, `GET /hires`, `GET /hires/{id}` (masked), `PUT /hires/{id}/pii/{field}`
(HR only), `POST /hires/{id}/pii/{field}/reveal` (reason required, short TTL hint),
`GET /pii-access-log` (HR_ADMIN); dedupe by blind index (`{org_id, email_hash}` unique); consent or
alternative ID for Aadhaar; managers see only their reports, always masked; PII endpoints never log
bodies.

**Done when.**
- [ ] After `POST /hires`, no raw fixture value appears in `new_hires` or in captured logs (scan test)
- [ ] MANAGER reveal → 403; HR reveal of Aadhaar → 403; HR_ADMIN Aadhaar reveal with reason → 200; missing reason → 422 (tests)
- [ ] Every reveal writes `pii_access_log`, readable through `GET /pii-access-log` (test)
- [ ] A duplicate email in the same org is rejected; the same email in another org is accepted (test)
- [ ] `make test-xt` covers every new route

### HIRE-2 · Store document scans encrypted; agents see only metadata
- **Size:** M · **Milestone:** M1 · **Label:** encrypted documents
- **Needs:** HIRE-1

**Outcome.** Aadhaar and PAN scans are encrypted before they reach GridFS, bound to the hire, and
`documents_meta` exposes only `type`, `uploaded_at` and `verified`.

**PRD.** FR-P3, §7.10 Documents, §10 `POST /hires/{id}/documents`.

**Done when.**
- [ ] GridFS bytes differ from the plaintext and decrypt only with the matching hire binding (test)
- [ ] Oversized files and disallowed MIME types are rejected (tests)
- [ ] A schema test shows `documents_meta` holds only ids, `type`, `uploaded_at` and `verified`
- [ ] `make test-xt` covers the new route

### OBS-1 · Scrub PII from every span and log before export
- **Size:** S · **Milestone:** M1 · **Label:** redaction processor
- **Needs:** F-4, PII-3

**Outcome.** A redaction span processor and logging filter, using the PII-3 detectors, run in every
service before export; tokens pass through, raw values never do.

**PRD.** §7.7 (redaction processor, content capture off), §7.10 Logs & traces.

**Done when.**
- [ ] A span with a mobile number, PAN and email in attributes and events exports with markers only (in-memory exporter test)
- [ ] A `ptk_` token passes through unchanged (test)
- [ ] A log record carrying the same values is scrubbed (test)
- [ ] `init_telemetry` installs the processor by default, asserted by a test

### MS-1 · Core platform ready
- **Size:** S · **Milestone:** M1 · **Label:** M1 gate
- **Needs:** MS-0, C-1, T-1, T-2, AUTH-1, AUD-1, T-3, PII-1, PII-3, PII-2, PII-4, HIRE-1, HIRE-2, OBS-1

**Outcome.** Auth, tenant isolation and the PII vault are real and tested, so every later goal builds
on them instead of around them.

**PRD.** §14 M1.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] `make check`, `make test-int` and `make test-xt` pass on `main` (output in `docs/evidence/MS-1/`)
- [ ] `docs/evidence/MS-1/walkthrough.md` records, over HTTP: HR logs in, creates a hire, sees masked fields, reveals one with a reason, and HR_ADMIN sees it in `GET /pii-access-log`
- [ ] `docker compose logs` grepped for every raw fixture PII value returns nothing (command and output recorded)

---

## M2 · Mongo MCP and the hub

### REG-1 · Declare every agent's allowed tools in a versioned registry
- **Size:** M · **Milestone:** M2 · **Label:** agent registry
- **Needs:** C-1, T-1, AUD-1

**Outcome.** Each agent's manifest (LLM, MCP tools, collections, memory scopes, approvals, limits)
is validated, versioned in `agent_registry`, audited on change and readable by HR_ADMIN.

**PRD.** FR-A1, §5.2, §7.3, U8.

**Scope.** `AgentManifest` model; validator that rejects any manifest naming `pii_vault*`,
`org_keys` or `pii_access_log`; `config/agents/*.yaml` for `template_builder`, `approvals_agent`,
`db_agent`, `templates_agent`, `provisioning_agent`, `run_assistant`; `tool_catalog` built from the
eval fixture's `action_catalog`; a loader that syncs YAML into the registry with a version bump and
an audit record; `GET /agents`, `GET /agents/{name}`.

**Done when.**
- [ ] A manifest listing a vault collection is rejected (test)
- [ ] Changing a YAML and re-syncing bumps the version and writes an audit record (test)
- [ ] All six manifests validate, and the catalog matches `evals/fixtures/seed_v1.json` `action_catalog` (test)
- [ ] `GET /agents` is HR_ADMIN only (role test)

### MCP-1 · Serve MongoDB tools over MCP with strict contracts
- **Size:** M · **Milestone:** M2 · **Label:** Mongo MCP server
- **Needs:** F-2, C-1

**Outcome.** `apps/mcp-mongo` (FastMCP, streamable HTTP) serves `list_collections`, `find`, `count`,
`aggregate`, `insert_one` and `update_one`, each with a JSON-schema contract, with tenant scope taken
only from the hub-signed call context.

**PRD.** §7.5 (tool table, tenant scope from the token, spans).

**Done when.**
- [ ] An MCP client lists and calls every tool in an integration test against testcontainers Mongo
- [ ] `find` without `limit` or with `limit` > 100 is rejected (test)
- [ ] An agent-supplied `org_id` in a filter is rejected; inserts get `org_id` from the context (tests)
- [ ] Each call's span carries `db.operation`, `db.collection`, `agent.name`, `org_id` and a result count, and no document content (in-memory exporter test)

### MCP-2 · Reject dangerous and cross-tenant queries
- **Size:** M · **Milestone:** M2 · **Label:** query validator
- **Needs:** MCP-1

**Outcome.** A recursive validator accepts only allowlisted operators and stages, rejects every
forbidden one, keeps `pii.*` read-only and never lets `org_id` be set.

**PRD.** §7.5 (validator, `pii.*` read-only, no destructive tools), §8.1 dangerous queries.

**Done when.**
- [ ] An injection corpus of 40+ payloads (nested, dotted, unicode, `$where`, `$function`, `$accumulator`, `$expr` with JS, `$lookup`, `$graphLookup`, `$out`, `$merge`, `$unionWith`) is all rejected, each with a named reason (test)
- [ ] A hypothesis test never produces an accepted query containing a forbidden operator
- [ ] An update touching `pii.mobile` and a `$set` of `org_id` are rejected (tests)
- [ ] A test lists the tools served by the app main() starts (over a real MCP session, through the production settings path) and asserts they are exactly the six PRD tools, so no delete, drop, index, admin or `runCommand` tool exists; how the process is started and what else it runs is left to code review and SEC-2

### MCP-3 · Back the policy with least-privilege database users
- **Size:** M · **Milestone:** M2 · **Label:** least-privilege DB users
- **Needs:** MCP-1

**Outcome.** Even with a policy bug, the MCP server can't exceed what MongoDB itself allows: separate
read and read-write users on named collections, and no MCP user can touch the vault.

**PRD.** §7.5 Defence in depth.

**Done when.**
- [ ] With the policy layer bypassed, a write using the read-only user is refused by MongoDB (test)
- [ ] Reading `pii_vault`, `pii_vault_aadhaar`, `org_keys`, `pii_access_log` or `users` with any MCP user is refused by MongoDB (test)
- [ ] The role definitions (`db.getUser` output) are recorded in `docs/evidence/MCP-3/`

### HUB-1 · Route every agent tool call through one policy gateway
- **Size:** L · **Milestone:** M2 · **Label:** MCP hub
- **Needs:** REG-1, MCP-2, MCP-3, OBS-1

**Outcome.** `apps/mcp-hub` is the single entry point for agent tools: it verifies a short-lived
agent token, checks the manifest, validates arguments, injects tenant scope and credentials, scans
responses for PII, audits and traces every call, and hard-denies the vault.

**PRD.** §5.2, §7.4, §8.1 (Tool rows), U8, FR-A2 (denial data).

**Scope.** Agent token (JWS: `agent`, `run_id`, `org_id`, `scopes`, `exp ≤ 15 min`) issued by a lib
the orchestrator uses; pipeline verify → manifest → JSON schema → tenant injection → credentials →
call → PII scan → audit → span; `requires_approval` surfaced to the caller; every denial audited with
`outcome=denied`; MCP tools added to `make test-xt`.

**Done when.**
- [ ] Expired token, tool outside the manifest, collection outside the manifest, and a vault collection under a forged manifest are each denied with an audit record and a `outcome=denied` span (named tests)
- [ ] An allowed call is scoped to the token's org even when its args name another org (test)
- [ ] A downstream response containing a raw mobile number is blocked or redacted before reaching the agent (test)
- [ ] Downstream credentials never appear in tool responses or agent-side config (test)
- [ ] `make test-xt` covers every hub tool

### MOCK-1 · Provide thin Bitbucket and provisioning mocks behind the hub
- **Size:** S · **Milestone:** M2 · **Label:** integration mocks
- **Needs:** HUB-1

**Outcome.** `apps/mcp-mocks` serves deterministic, idempotent Bitbucket and provisioning tools, and
a Telegram transport stub that NOTIF-1's sink uses; nothing calls a real external API.

**PRD.** §2 Non-goals (mocks), §7 diagram (`mcp-mocks`), §6.6.

**Done when.**
- [ ] The `provisioning_agent` token can call the mocks through the hub (integration test)
- [ ] Replaying a call with the same idempotency key returns the same result and creates nothing new (test)
- [ ] An agent without the tool in its manifest is denied (test)

### MS-2 · Real Mongo MCP behind the hub
- **Size:** S · **Milestone:** M2 · **Label:** M2 gate
- **Needs:** MS-1, REG-1, MCP-1, MCP-2, MCP-3, HUB-1, MOCK-1

**Outcome.** Agents can reach exactly the data their manifest allows, and nothing else, with every
attempt audited and traced.

**PRD.** §14 M2.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] `make check`, `make test-int` and `make test-xt` pass on `main`
- [ ] `scripts/demo/mcp_probe.py`, run with a `db_agent` token on the compose stack, shows an allowed `find` succeed and cross-tenant, injection and vault attempts denied, each with an audit record (output in `docs/evidence/MS-2/`)
- [ ] A Jaeger trace export shows hub → mongo spans carrying `outcome` (saved in `docs/evidence/MS-2/`)

---

## M3 · LLM connectors, templates, evals

### LLM-1 · Plug LLM providers in through a connector registry
- **Size:** M · **Milestone:** M3 · **Label:** LLM connectors
- **Needs:** F-1, F-4

**Outcome.** `libs/py-llm` resolves a LangChain `BaseChatModel` for any agent from configuration,
through connectors discovered by entry point (`bedrock`, `langchain`, `fake`), with no credentials or
model ids in code.

**PRD.** §5.6, §7.1 LLM connector layer, U10, §12 Portability.

**Scope.** `LLMConnector` protocol; entry-point group `onboarding.llm_connectors`; `bedrock`
(`ChatBedrockConverse`, optional guardrail config), `langchain` (`init_chat_model`), `fake`
(scripted, deterministic, tool-calling); `config/llm.yaml` with `${ENV}` interpolation; resolution
agent manifest → org override → default; fail-fast env validation; span attributes `llm.connector`,
`llm.model` and token usage; `make llm-health CONNECTOR=…`.

**Done when.**
- [ ] Resolution order has three tests (manifest wins, org override, global default)
- [ ] A test-only connector package registered by entry point is discovered with no core change (test)
- [ ] Selecting `bedrock` without `AWS_REGION` fails at startup with a clear message (test)
- [ ] The `fake` connector replays a scripted tool-calling conversation (test)
- [ ] `git grep` finds no model ids outside `config/`, `.env.example` and tests (command recorded)

### OW-1 · Get Bedrock access and choose the three model aliases
- **Size:** S · **Milestone:** M3 · **Label:** Bedrock access
- **Needs:** LLM-1

**Outcome.** The `bedrock` connector can call real models: an AWS identity with Bedrock access in the
chosen region, and model ids for `fast`, `standard` and `deep`, all in the local `.env` only.

**PRD.** §7.1 (bedrock connector, credentials from env), §16 Decided v0.2.

**Scope.** Enable model access; create a least-privileged IAM identity (`bedrock:InvokeModel*`,
`bedrock:Converse*`); optionally a Bedrock Guardrail id; record choices (never keys).

**Done when.**
- [ ] `docs/evidence/OW-1/models.md` names the region, the three model ids and the date, and holds no secrets
- [ ] `make llm-health CONNECTOR=bedrock` succeeds for all three aliases (output recorded)
- [ ] A secret scan of the tree finds no AWS keys (command and output recorded)

### GR-1 · Guard every LLM input, output and budget
- **Size:** M · **Milestone:** M3 · **Label:** guardrails lib
- **Needs:** PII-3, AUD-1, F-4

**Outcome.** `libs/py-guardrails` checks every LLM input and output and every budget, connector
agnostic, with each decision audited and emitted as a span event; Bedrock Guardrails are additive.

**PRD.** §5.3, §8.1.

**Scope.** Untrusted-content delimiting; heuristic prompt-injection detector; free-text PII redaction
before the LLM; output PII scan; structured-output validation with one repair retry, then a visible
failure; step/token/time budgets; optional Bedrock Guardrails adapter.

**Done when.**
- [ ] An injection corpus (20+ known patterns) is detected; the benign-set false-positive rate is recorded (test)
- [ ] Free text holding an Aadhaar and a PAN reaches the fake LLM only as markers (assert on captured input)
- [ ] Invalid structured output triggers exactly one repair attempt, then a visible failure (test)
- [ ] An exceeded budget stops the loop (test)
- [ ] Every decision appears as a `guardrail.outcome` span event and an audit record (test)

### TPL-1 · Validate templates into safe task graphs
- **Size:** M · **Milestone:** M3 · **Label:** template validator
- **Needs:** REG-1, PII-4

**Outcome.** A template schema and validator that enforce every FR-T3 rule, with a safe expression
parser for `when` conditions and a seed template that passes.

**PRD.** FR-T3, §5.1, §6.1 template shape.

**Scope.** Pydantic template model (variables, tasks, approver rules, `when`, `sla`, `inputs`,
`pii_access`, `requires_approval`); validator; an expression parser with no `eval`;
`templates/engineer-india.yaml`.

**Done when.**
- [ ] One named failing test per FR-T3 rule: cycle, unknown agent, tool outside the allowlist, undeclared variable, approval without approver, sensitive field in LLM-visible inputs, `pii_access` not allowed by the sink policy
- [ ] The seed template and every template in `evals/fixtures/seed_v1.json` validate (test)
- [ ] The `when` parser rejects function calls, attribute access and imports (test)
- [ ] `pii_access` for Aadhaar is rejected (test)

### DAG-1 · Compile a template version and hire variables into a pruned DAG
- **Size:** M · **Milestone:** M3 · **Label:** DAG compiler
- **Needs:** TPL-1

**Outcome.** A pure compiler turns a published template version plus a hire's variables into a
deterministic, pruned DAG; tasks whose `when` fails are `skipped` and never block their dependents.

**PRD.** §5.1, §7.1 Compilation, FR-R3.

**Done when.**
- [ ] Engineer variables keep `bitbucket_access`; sales variables mark it skipped with a reason (test)
- [ ] A property test shows every compiled graph is acyclic and every dependent of a skipped task stays schedulable
- [ ] Compiling twice gives the same hash (test)
- [ ] `sla: start_date - 7d` resolves to the right date (test)

### TPL-2 · Run the template lifecycle: draft, validate, publish, archive
- **Size:** M · **Milestone:** M3 · **Label:** template lifecycle
- **Needs:** TPL-1, AUTH-1, T-3, AUD-1

**Outcome.** Drafts are edited and validated, only HR_ADMIN publishes, published versions never
change, and old versions can be archived.

**PRD.** FR-T4, §10 Templates.

**Scope.** `templates`, `template_versions` (immutable), `template_drafts`;
`GET/PUT /templates/drafts/{id}`, `POST /templates/drafts/{id}/validate`,
`POST /templates/drafts/{id}/publish`, `GET /templates`, archive; publish audited.

**Done when.**
- [ ] HR publish → 403; HR_ADMIN publish → 200 (test)
- [ ] Publishing an invalid draft returns 422 with the validator's errors (test)
- [ ] An attempt to change a published version fails (test)
- [ ] `make test-xt` covers every new route

### SEED-1 · Seed an org, users and templates with one command
- **Size:** S · **Milestone:** M3 · **Label:** make seed
- **Needs:** TPL-2, REG-1, AUTH-1

**Outcome.** `make seed` creates the org, an HR_ADMIN, an HR user, sample managers, the agent
registry and the published seed template.

**PRD.** §7.11 (`make seed`).

**Done when.**
- [ ] On an empty database, `make seed` creates every listed record (test or recorded output)
- [ ] A second run changes nothing (test)
- [ ] Seed passwords come from env; none are committed (`git grep` recorded)

### EVAL-1 · Build the deterministic eval harness
- **Size:** L · **Milestone:** M3 · **Label:** eval harness
- **Needs:** LLM-1, PII-2

**Outcome.** `libs/py-evals` runs the JSONL cases in `evals/` against a fresh fixture each time,
grades them by code only, and reports pass@1, pass^k and the gates.

**PRD.** §8.2, `evals/README.md`.

**Scope.** Case loader; fixture loader into a throwaway Mongo (encrypting `pii_vault_plain` through
the real vault); every check in the README catalog; capture layer (tool calls including denied ones,
LLM inputs, steps, tokens, latency, MCP audit); `nx run evals:run --agent --llm --repeat`; metrics,
gates and regression-vs-baseline; reports in `evals/reports/` and `eval_runs`; spans with
`eval.run_id` and `eval.case_id`; `make evals-selftest` with the `fake` connector in CI.

**Done when.**
- [ ] Every check in the `evals/README.md` catalog has a unit test with one passing and one failing example
- [ ] `make evals-selftest` runs the checks of all 27 cases against scripted outputs and passes (in CI or recorded)
- [ ] Gate computation is tested: one failed safety repetition blocks; capability thresholds apply as written
- [ ] A run writes a report to `evals/reports/` and an `eval_runs` record (test)

### AG-1 · Run any registered agent with only its allowed tools
- **Size:** L · **Milestone:** M3 · **Label:** agent runtime
- **Needs:** LLM-1, HUB-1, GR-1

**Outcome.** `libs/py-agents` builds any agent from its manifest as a LangGraph subgraph: model from
`py-llm`, tools from the hub through `langchain-mcp-adapters`, guardrails around every LLM call, a
typed output contract, and budgets.

**PRD.** §5.2, §7.1 (Sub-agents, Budgets), §8.2 output contracts.

**Scope.** Subgraph factory; per-task agent token; local tools (`date_calc`, `render`); output
contracts for every agent in `libs/py-contracts` (shapes from `evals/README.md`); `status: ok |
refused | needs_human`; out-of-band tool context for grants (never in the prompt).

**Done when.**
- [ ] With the `fake` connector, an agent calling a tool outside its manifest is denied by the hub and the denial appears in its trajectory (test)
- [ ] The grant and agent token never appear in captured LLM input (test)
- [ ] An exhausted step budget ends the run with a visible failure (test)
- [ ] Every agent output contract has a schema test

### TB-1 · Draft templates by chatting with the Template Builder
- **Size:** M · **Milestone:** M3 · **Label:** Template Builder
- **Needs:** AG-1, TPL-2, EVAL-1, OW-1

**Outcome.** HR describes a flow in plain language and gets a validated template draft plus a short
explanation; the Builder can read the registry and write drafts, and nothing else.

**PRD.** FR-T1, FR-T5, U1, §10 `POST /templates/drafts/chat`.

**Done when.**
- [ ] `tb-001`…`tb-006` meet the eval gates on the real connector (`--llm standard --repeat 3` report in `docs/evidence/TB-1/`)
- [ ] With the `fake` connector, the chat endpoint streams SSE events and stores a valid draft (test)
- [ ] A Builder attempt to publish or start a run is denied (test)

### TB-2 · Let managers suggest changes and HR review them in a queue
- **Size:** M · **Milestone:** M3 · **Label:** change requests
- **Needs:** TB-1

**Outcome.** A manager chats with the Builder in suggest mode and gets a change request (diff +
rationale); it lands in a separate HR queue where HR accepts it into a draft or rejects it with a
reason.

**PRD.** FR-T6, U9, §10 Change requests.

**Done when.**
- [ ] `tbs-001` and `tbs-002` meet the eval gates on the real connector (report in `docs/evidence/TB-2/`)
- [ ] Accepting creates a draft equal to base + diff (test)
- [ ] A manager cannot accept a change request (403); rejecting requires a reason (tests)
- [ ] Managers see only their own change requests in `GET /change-requests` (test)
- [ ] `make test-xt` covers every new route

### MS-3 · Templates drafted, validated and published by the agent
- **Size:** S · **Milestone:** M3 · **Label:** M3 gate
- **Needs:** MS-2, LLM-1, OW-1, GR-1, TPL-1, DAG-1, TPL-2, SEED-1, EVAL-1, AG-1, TB-1, TB-2

**Outcome.** The agentic Template Studio works end to end over the API, and agent quality is measured
by the harness.

**PRD.** §14 M3.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] `make check`, `make test-int`, `make test-xt` and `make evals-selftest` pass on `main`
- [ ] `docs/evidence/MS-3/walkthrough.md` records chat → draft → validate → publish over HTTP on the real connector
- [ ] The DEC-1 row in ACCEPTANCE.md is `pass`

---

## M4 · Runtime: event bus, orchestrator, approvals

### EVT-1 · Move work through a durable Mongo event bus
- **Size:** L · **Milestone:** M4 · **Label:** event bus
- **Needs:** T-1, F-4

**Outcome.** `libs/py-eventbus` writes a domain change and its event in one transaction and hands
work to consumers through a leased, retried, dead-lettered job queue that always carries `org_id`
and the trace.

**PRD.** §7.2, §7.9 Event bus, §12 Durability.

**Scope.** `EventBus` interface; append-only `events` with a per-org idempotency key; outbox helper;
`jobs` with `findOneAndUpdate` leases, heartbeat, attempts and exponential backoff; `dead_letters`;
consumers set `TenantContext` from the job; a job without `org_id` is dead-lettered; `traceparent`
stored and continued; consumers registered in `make test-xt`.

**Done when.**
- [ ] Two workers never process the same job at once (concurrency test)
- [ ] A crashed worker's lease expires and the job is retried; exhausted jobs move to `dead_letters` (tests)
- [ ] A duplicate idempotency key is ignored; a rolled-back transaction leaves neither change nor event (tests)
- [ ] A consumer's span joins the producer's trace (test)
- [ ] A job without `org_id` is dead-lettered (test)

### RUN-1 · Start a run that compiles the DAG and executes it in LangGraph
- **Size:** L · **Milestone:** M4 · **Label:** orchestrator core
- **Needs:** EVT-1, DAG-1, HIRE-1, TPL-2

**Outcome.** `POST /runs` pins a published template version, compiles the DAG and starts a run that
`apps/orchestrator` executes as a LangGraph `StateGraph` with a Mongo checkpointer, running
independent tasks in parallel.

**PRD.** FR-R1, FR-R2, FR-R3, §7.1 (Compilation, Durability), §7.9 Orchestrator.

**Scope.** Run, `run_tasks` and `run.started` written through the outbox; worker consumer; graph
builder (node per task, edges from `depends_on`, fan-out and fan-in); `thread_id = org_id:run_id`;
task state machine; a stub executor (agents arrive in RUN-3).

**Done when.**
- [ ] In a diamond DAG, B and C run in parallel (overlapping timestamps) and D starts only after both (test)
- [ ] Skipped dependencies don't block; illegal state transitions are rejected (tests)
- [ ] The checkpoint `thread_id` is `org_id:run_id` (test)
- [ ] A run stays pinned to its version after a newer version is published (test)
- [ ] `make test-xt` covers `POST /runs`

### RUN-2 · Survive restarts and let HR control a run
- **Size:** M · **Milestone:** M4 · **Label:** run control
- **Needs:** RUN-1, AUD-1

**Outcome.** Runs survive worker restarts; failed tasks retry and then block the run with HR
notified; HR pauses, resumes, cancels, retries and manually completes with an audited reason; runs
stream live.

**PRD.** FR-R4, FR-R5, FR-R6, U4, §10 runs endpoints and `GET /runs/{id}/stream`.

**Done when.**
- [ ] A worker killed with SIGKILL mid-run restarts and the run completes; a counter proves no task's side effect ran twice (test)
- [ ] A task failing past its max attempts blocks the run and records an HR notification (test)
- [ ] Manual complete without a reason → 422; with one → audited (tests)
- [ ] Managers list only their reports' runs (test)
- [ ] The SSE stream emits each task transition (test)

### RUN-3 · Run task nodes through registered agents with scoped tokens and grants
- **Size:** L · **Milestone:** M4 · **Label:** agents in runs
- **Needs:** RUN-1, AG-1, PII-4, MOCK-1

**Outcome.** Each task node runs its registered agent with a per-task agent token, and gets a PII
grant only when its template task declares `pii_access`; the model used is recorded on the task.

**PRD.** §7.1 (Sub-agents, Budgets, model recorded on the task), §7.10 grants at task start, §7.9
Orchestrator, §5.7.

**Done when.**
- [ ] A task without `pii_access` gets no grant; a task with it gets a grant limited to its fields and sink (tests)
- [ ] The model is recorded on the task and doesn't change when config changes mid-run (test)
- [ ] An exhausted per-run token budget fails the task visibly (test)
- [ ] A run's spans form run → task → agent → LLM/tool (in-memory exporter tree test)

### NOTIF-1 · Notify in-app and through the Telegram mock, resolving PII only at the sink
- **Size:** M · **Milestone:** M4 · **Label:** notifications + sink
- **Needs:** MOCK-1, PII-4, EVT-1

**Outcome.** The notification tool sends through channel adapters: in-app, and a Telegram mock whose
sink verifies the grant, resolves the token, "sends", and stores only a masked copy; the webhook
endpoint has the real Telegram callback contract.

**PRD.** FR-N1, FR-N2, FR-H2 (channel), §7.10 sink flow, §10 Mock channels.

**Scope.** `notify` tool with `in_app` and `telegram_mock` adapters; `mock_outbox`;
`GET /mock-outbox`; `POST /webhooks/telegram` (secret-token header, `callback_query` shape) emitting
a decision event; change-request outcomes (TB-2) and blocked runs (RUN-2) delivered in-app.

**Done when.**
- [ ] `mock_outbox` holds only the masked mobile, never the raw value (DB scan test)
- [ ] A rejected grant stores nothing, fails the task, and writes a guardrail event and an audit record (test)
- [ ] The webhook rejects a wrong secret token, and accepts a payload matching Telegram's documented `callback_query` (fixture test)
- [ ] `make test-xt` covers every new route

### AGT-1 · Ship the DB, Templates and Provisioning agents
- **Size:** M · **Milestone:** M4 · **Label:** task agents
- **Needs:** RUN-3, NOTIF-1, EVAL-1

**Outcome.** `db_agent`, `templates_agent` and `provisioning_agent` do their seed-template tasks
inside runs and meet their eval gates.

**PRD.** §7 Agent Hub, §8.2, §13 (≥ 70% of tasks without human action).

**Done when.**
- [ ] `db-001`…`db-006` and `tp-001`…`tp-003` meet the eval gates on the real connector (report in `docs/evidence/AGT-1/`)
- [ ] A provisioning task completes through the mocks in an integration run (test)
- [ ] An integration run of the seed template completes every non-approval task without human action; the count is recorded in the handoff

### HIL-1 · Pause for approvals and resume on the first decision
- **Size:** L · **Milestone:** M4 · **Label:** approvals (HIL)
- **Needs:** RUN-3, NOTIF-1, EVAL-1

**Outcome.** Approval tasks pause their branch with `interrupt()`; the Approvals Agent prepares a
masked packet; the approver is notified in-app and through the Telegram mock; the first decision
wins and resumes the run; `requires_approval` tool calls become approval tasks.

**PRD.** FR-H1, FR-H2, FR-H3, FR-H5, U5, §7.1 HIL, §10 Approvals.

**Done when.**
- [ ] `ap-001`…`ap-003` meet the eval gates on the real connector (report in `docs/evidence/HIL-1/`)
- [ ] Concurrent web and Telegram decisions record exactly one; the other gets 409 (test)
- [ ] Decision → run resumed is under 5 s at p95 over 30+ decisions on the compose stack (measurement in `docs/evidence/HIL-1/`)
- [ ] A `requires_approval` tool call creates an approval task (test)
- [ ] `make test-xt` covers every new route

### HIL-2 · Remind and escalate overdue approvals
- **Size:** S · **Milestone:** M4 · **Label:** approval SLA
- **Needs:** HIL-1

**Outcome.** Overdue approvals remind the approver once, then escalate to HR.

**PRD.** FR-H4.

**Done when.**
- [ ] With a fake clock, an overdue approval sends one reminder, then escalates to HR at the second threshold (test)
- [ ] Thresholds come from config (test)
- [ ] Reminders aren't repeated after a worker restart (test)

### CHAT-1 · Answer "why is this run stuck?" from run state
- **Size:** M · **Milestone:** M4 · **Label:** Run Assistant chat
- **Needs:** RUN-2, AG-1, EVAL-1

**Outcome.** HR and managers ask about a run; the Run Assistant answers from run state, task history
and the audit log through read-only tools, cites tasks, and changes nothing without explicit
confirmation.

**PRD.** FR-C1, FR-C2, U6, §7.6 Conversation, §10 Chat.

**Done when.**
- [ ] `ra-001`…`ra-007` meet the eval gates on the real connector (report in `docs/evidence/CHAT-1/`)
- [ ] A proposed retry runs only after the confirm call (test)
- [ ] A manager asking about a run outside their reports is refused (test)
- [ ] Stored messages contain redaction markers, not raw PII (test)
- [ ] `make test-xt` covers every new route

### MS-4 · Runs execute end to end with approvals
- **Size:** S · **Milestone:** M4 · **Label:** M4 gate
- **Needs:** MS-3, EVT-1, RUN-1, RUN-2, RUN-3, NOTIF-1, AGT-1, HIL-1, HIL-2, CHAT-1

**Outcome.** A hire's onboarding runs from start to finish on the compose stack, with one approval
through the Telegram mock, and survives a worker crash.

**PRD.** §14 M4.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] `make check`, `make test-int`, `make test-xt` and `make evals-selftest` pass on `main`
- [ ] `docs/evidence/MS-4/run.md` records: create hire → start run → approval through the mock Telegram webhook → run completes, with the trace id
- [ ] The NF-DUR row in ACCEPTANCE.md is `pass`

---

## M5 · Frontend

### UI-1 · Web shell with login, roles and masked fields
- **Size:** M · **Milestone:** M5 · **Label:** web shell
- **Needs:** C-1, AUTH-1

**Outcome.** `apps/web` (React, Vite, TypeScript, shadcn/ui, TanStack Query) logs in, shows
role-based navigation, renders every sensitive field through one masked component, and calls the API
only through the generated client.

**PRD.** §11 Frontend, §7.8, FR-P1.

**Scope.** Login; token handling decided in `docs/adr/0005-web-token-storage.md`; role-based nav;
`MaskedField`; envelope-aware error handling; served through Caddy in dev; Playwright set up.

**Done when.**
- [ ] Vitest tests cover `MaskedField` and role-based nav
- [ ] A Playwright smoke test logs in as each role and sees the right nav
- [ ] `make check` runs web lint, typecheck and tests
- [ ] The token-storage ADR exists

### UI-2 · Template Studio: chat, DAG editor and form view
- **Size:** L · **Milestone:** M5 · **Label:** Template Studio
- **Needs:** UI-1, TB-1

**Outcome.** HR chats with the Builder, sees the draft as an editable React Flow DAG and as a form,
fixes validation errors inline, and HR_ADMIN publishes.

**PRD.** FR-T1, FR-T2, U1, U2.

**Done when.**
- [ ] Playwright: HR chats, sees the draft DAG, edits a task in the form, sees an inline validation error, and HR_ADMIN publishes
- [ ] Vitest: DAG ↔ form round trip preserves the template exactly

### UI-3 · Change requests: manager suggest mode and HR queue
- **Size:** M · **Milestone:** M5 · **Label:** change-request UI
- **Needs:** UI-2, TB-2

**Outcome.** A manager suggests a change from a published template; HR works the separate queue,
accepting into a draft or rejecting with a reason; the manager sees the outcome.

**PRD.** FR-T6, U9.

**Done when.**
- [ ] Playwright: a manager suggests, HR accepts into a draft, and the manager sees the outcome
- [ ] Playwright: HR rejects with a reason and the manager sees it

### UI-4 · New hires with PII entry, masking and audited reveal
- **Size:** M · **Milestone:** M5 · **Label:** hires UI
- **Needs:** UI-1, HIRE-2

**Outcome.** HR adds a hire with sensitive fields, sees them masked, reveals one with a reason for a
short time, and uploads documents whose metadata is shown.

**PRD.** FR-P1, FR-P2, FR-P3, FR-P4, U3.

**Done when.**
- [ ] Playwright: HR creates a hire, sees masked fields, reveals one with a reason, and it hides again after the TTL
- [ ] Playwright: a manager sees no reveal control; HR sees no Aadhaar reveal control
- [ ] Playwright: a document upload shows its type, upload time and verified flag

### UI-5 · Run board with live status, blocked reasons and trace links
- **Size:** M · **Milestone:** M5 · **Label:** run board
- **Needs:** UI-1, RUN-2

**Outcome.** HR sees every run's progress live, which task is blocked and why, acts on it, and opens
the run's trace in Jaeger.

**PRD.** U4, U7, FR-R5.

**Done when.**
- [ ] Playwright: run detail updates live (SSE) as tasks change
- [ ] Playwright: a blocked task shows its reason, and retry / complete-with-reason work
- [ ] The trace link opens Jaeger on the run's trace id (Playwright asserts the URL)

### UI-6 · Approvals inbox and mock Telegram outbox
- **Size:** M · **Milestone:** M5 · **Label:** approvals UI
- **Needs:** UI-1, HIL-1, NOTIF-1

**Outcome.** Approvers decide from the web inbox or from the mock outbox's Approve/Reject buttons,
which hit the same webhook a real Telegram callback would.

**PRD.** FR-H2, FR-N2, U5.

**Done when.**
- [ ] Playwright: approving from the inbox resumes the run
- [ ] Playwright: the mock outbox's Approve button calls `POST /webhooks/telegram` and resumes the run
- [ ] Playwright: a second decision shows "already decided"

### UI-7 · Chat with the orchestrator on the run page
- **Size:** S · **Milestone:** M5 · **Label:** run chat UI
- **Needs:** UI-5, CHAT-1

**Outcome.** A chat panel on the run page answers with links to the cited tasks and asks for
confirmation before any action.

**PRD.** U6, FR-C1, FR-C2.

**Done when.**
- [ ] Playwright: a question gets an answer whose citations link to tasks
- [ ] Playwright: a proposed action shows a confirm dialog and runs only on confirm

### UI-8 · Admin views: agents, denials, audit, PII access, evals
- **Size:** M · **Milestone:** M5 · **Label:** admin UI
- **Needs:** UI-1, REG-1, HUB-1, AUD-1, HIRE-1, EVAL-1

**Outcome.** HR_ADMIN sees each agent's connector, model, allowed tools and recent denials, plus the
audit log, the PII access log and eval runs (read-only).

**PRD.** FR-A2, U8, §10 (Registry & audit, Evals).

**Scope.** Includes `GET /evals/runs`, `GET /evals/runs/{id}` and a recent-denials query per agent.

**Done when.**
- [ ] Playwright: HR_ADMIN opens each admin view and sees seeded data
- [ ] HR and MANAGER get 403 on every admin route (test)
- [ ] An agent's page shows its connector, model, allowed tools and recent denials from the audit log (Playwright)
- [ ] `make test-xt` covers the eval routes

### MS-5 · Every MVP screen works against the real backend
- **Size:** S · **Milestone:** M5 · **Label:** M5 gate
- **Needs:** MS-4, UI-1, UI-2, UI-3, UI-4, UI-5, UI-6, UI-7, UI-8

**Outcome.** HR, HR_ADMIN and managers can do every user story in the browser.

**PRD.** §14 M5, §4.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] `make check`, `make test-int`, `make test-xt` and the Playwright suite pass on `main`
- [ ] `docs/evidence/MS-5/stories.md` maps U1–U10 to the Playwright test or manual step that shows each one

---

## M6 · Hardening, deployment, demo

### PII-5 · Purge sensitive values 90 days after onboarding completes
- **Size:** S · **Milestone:** M6 · **Label:** PII retention purge
- **Needs:** EVT-1, RUN-2, PII-2

**Outcome.** A scheduled job deletes vault ciphertext 90 days after a run completes, keeps the record
as `purged`, and keeps the consent/purpose record.

**PRD.** FR-P6, §7.10 Retention.

**Done when.**
- [ ] With a fake clock: on day 89 values resolve; on day 90 the job purges and tokens resolve to `[purged]` (test)
- [ ] The consent/purpose record survives the purge (test)
- [ ] Each purge is audited and the job is idempotent (tests)

### SEC-1 · Prove zero PII leaks and 100% tamper rejection
- **Size:** M · **Milestone:** M6 · **Label:** PII leak + tamper suite
- **Needs:** HIL-1, CHAT-1, AGT-1, OBS-1, PII-5

**Outcome.** `make test-pii` runs a full run with fixture PII and finds no raw value anywhere outside
the vault, and every tamper and forged-grant case is rejected.

**PRD.** §12 (PII, PII integrity), §15, §13 (trace with no PII).

**Scope.** Scan every Mongo collection except the vault, every container log, a Jaeger trace export,
`mock_outbox`, events and captured LLM inputs for each raw fixture value and every detector hit;
gather the vault, grant, token and document tamper tests into one report.

**Done when.**
- [ ] `make test-pii` reports zero hits (report in `docs/evidence/SEC-1/`)
- [ ] A planted leak is caught by the scan (mutation check, output recorded)
- [ ] The tamper report lists every case with 100% rejected
- [ ] CI runs `make test-pii`

### SEC-2 · Security review and hardening pass
- **Size:** M · **Milestone:** M6 · **Label:** security review
- **Needs:** MS-5

**Outcome.** A whole-repo security review with every critical and high finding fixed, and secret
and dependency scanning in CI.

**PRD.** §12 Security, §15.

**Scope.** `security-reviewer` over the repo; gitleaks, `pip-audit` and `npm audit` in CI; agent
tokens capped at 15 minutes; boundary validation review; login rate limiting (your standing security
rule, not in the PRD: record it in `docs/adr/0003-login-rate-limit.md`).

**Done when.**
- [ ] `docs/evidence/SEC-2/review.md` lists every finding; all critical and high are fixed
- [ ] gitleaks, `pip-audit` and `npm audit` run in CI and pass (run link or recorded output)
- [ ] An agent token with `exp` beyond 15 minutes is rejected by the hub (test)
- [ ] Login is rate-limited (test) and the ADR exists

### EVAL-2 · Meet every eval gate on the real model and record a baseline
- **Size:** M · **Milestone:** M6 · **Label:** eval baseline
- **Needs:** AGT-1, HIL-1, CHAT-1, TB-2

**Outcome.** Every agent meets the eval gates in one full run on the `standard` alias; the result is
the baseline that later changes are compared with; every bug found so far is a regression case.

**PRD.** §8.2 (Metrics, Gates, regression cases), §12 Agent quality.

**Done when.**
- [ ] A full `--repeat 3` run meets the gates for all six agents (report in `docs/evidence/EVAL-2/`)
- [ ] A baseline is committed under `evals/baselines/`
- [ ] The regression check fails when a fake drop of more than 5 points is injected (test)
- [ ] Every agent bug fixed during M3–M5 has a `regression` case (list in the handoff)

### PERF-1 · Measure API latency and approval-to-resume time
- **Size:** S · **Milestone:** M6 · **Label:** latency budgets
- **Needs:** HIL-1, RUN-2

**Outcome.** The PRD latency targets are measured, with the method written down.

**PRD.** §12 Latency.

**Done when.**
- [ ] `docs/evidence/PERF-1/latency.md` states the tool, hardware, sample size and endpoints, and non-LLM endpoints have p95 < 300 ms
- [ ] Approval decision → run resumed has p95 < 5 s over 30+ decisions (same file)

### E2E-1 · Automate the golden path end to end
- **Size:** L · **Milestone:** M6 · **Label:** golden-path E2E
- **Needs:** MS-5, SEED-1

**Outcome.** `make e2e` runs the PRD's golden path in the browser against the compose stack.

**PRD.** §13 golden path, §12 Quality.

**Done when.**
- [ ] `make e2e` (Playwright) passes: the agent drafts a template → a manager suggestion is accepted → HR_ADMIN publishes → a hire is added → the run completes with one approval through the Telegram mock
- [ ] The test fetches the run's trace from Jaeger's API and the PII scanner finds nothing in it
- [ ] It runs in CI, or the handoff records a local run with output

### DEP-1 · Deploy to the VM with TLS, backups and a runbook
- **Size:** M · **Milestone:** M6 · **Label:** VM deployment
- **Needs:** OW-2, MS-5, SEED-1

**Outcome.** The `vm` profile runs on the VM with built images; only Caddy is reachable, over TLS;
backups run nightly and a restore has been rehearsed.

**PRD.** §7.11.

**Done when.**
- [ ] An external port scan of the VM shows only 22, 80 and 443 (output recorded)
- [ ] HTTPS serves a valid certificate; Jaeger needs basic auth (recorded)
- [ ] A service started without a required env var fails fast (recorded)
- [ ] A nightly `mongodump` has run, and a restore into a scratch container was rehearsed (`docs/runbooks/deploy.md` + evidence)
- [ ] `make seed` ran on the VM

### OW-2 · Provision the VM and domain
- **Size:** S · **Milestone:** M6 · **Label:** VM + domain
- **Needs:** —

**Outcome.** A VM with Docker and Compose, SSH by key only, a firewall allowing 22/80/443, and a
DNS record for the app's domain.

**PRD.** §7.11, §16 (VM sizing out of scope: pick what's convenient).

**Done when.**
- [ ] `docs/evidence/OW-2/vm.md` records provider, size, OS, domain and date, and no secrets (closes DEC-2)
- [ ] `ssh <host> docker compose version` output is recorded
- [ ] `dig +short <domain>` returns the VM's IP (recorded)

### DEMO-1 · Run the success-metric demos on the VM
- **Size:** S · **Milestone:** M6 · **Label:** success demos
- **Needs:** DEP-1, E2E-1, EVAL-2, SEC-1, SEC-2, PERF-1

**Outcome.** Every PRD §13 success metric is shown on the VM and written down.

**PRD.** §13, U7, U10.

**Done when.**
- [ ] The golden path runs on the VM, with the run id and trace id recorded in `docs/evidence/DEMO-1/`
- [ ] At least 70% of the seed template's tasks completed without human action, excluding approvals (count from `run_tasks`, recorded)
- [ ] A probe script shows zero successful cross-tenant or cross-agent accesses and an audit record for every attempt (output recorded)
- [ ] After changing the `standard` alias in config, the next task's recorded model and its `llm.model` span attribute show the new model (recorded)

### MS-6 · MVP complete
- **Size:** S · **Milestone:** M6 · **Label:** MVP complete
- **Needs:** MS-5, PII-5, SEC-1, SEC-2, EVAL-2, PERF-1, E2E-1, DEP-1, OW-2, DEMO-1

**Outcome.** The MVP described in PRD v0.3 is built, proven and running on the VM.

**PRD.** §12, §13, §14 M6.

**Exit checks.**
- [ ] Every need is Done on the board
- [ ] Every ACCEPTANCE.md row gated at MS-6 is `pass` or `n/a` with a reason
- [ ] `make check`, `make test-int`, `make test-xt`, `make test-pii`, `make evals-selftest` and `make e2e` pass on `main` (output in `docs/evidence/MS-6/`)
- [ ] The PROD rows (DEC-3, DEC-4) are still listed as open before any production use
